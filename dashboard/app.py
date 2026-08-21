import sqlite3
import os
import secrets
import json
from fastapi import FastAPI, Depends, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
from passlib.context import CryptContext

app = FastAPI(title="Work & Salary Tracker")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = "data/dashboard.db"

WORK_TYPES = {
    "off": {"days": 0.0, "multiplier": 0.0, "label": "休假"},
    "half": {"days": 0.5, "multiplier": 0.5, "label": "半天"},
    "full": {"days": 1.0, "multiplier": 1.0, "label": "全天"},
    "ot_1.5": {"days": 1.5, "multiplier": 1.5, "label": "加班 1.5x"},
    "ot_2.0": {"days": 2.0, "multiplier": 2.0, "label": "加班 2.0x"},
    "ot_3.0": {"days": 3.0, "multiplier": 3.0, "label": "加班 3.0x"},
}

def init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            session_token TEXT,
            is_admin INTEGER DEFAULT 0
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            username TEXT PRIMARY KEY,
            pay_mode TEXT DEFAULT 'day',
            default_rate REAL DEFAULT 700.0,
            currency TEXT DEFAULT '$',
            companies TEXT DEFAULT '預設公司'
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS work_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            date TEXT NOT NULL,
            pay_mode TEXT NOT NULL,
            status TEXT NOT NULL,
            rate REAL NOT NULL,
            hours REAL DEFAULT 0,
            daily_pay REAL NOT NULL,
            work_days REAL NOT NULL DEFAULT 1.0,
            company TEXT DEFAULT '預設公司',
            location TEXT DEFAULT ''
        )
    """)
    
    # 檢查並補充舊欄位 (相容性)
    cursor.execute("PRAGMA table_info(user_settings)")
    cols = [col[1] for col in cursor.fetchall()]
    if 'companies' not in cols:
        cursor.execute("ALTER TABLE user_settings ADD COLUMN companies TEXT DEFAULT '預設公司'")
        
    cursor.execute("PRAGMA table_info(work_logs)")
    w_cols = [col[1] for col in cursor.fetchall()]
    if 'company' not in w_cols:
        cursor.execute("ALTER TABLE work_logs ADD COLUMN company TEXT DEFAULT '預設公司'")

    cursor.execute("SELECT username FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        admin_pwd_hash = pwd_context.hash("admin123")
        cursor.execute("INSERT INTO users (username, password_hash, is_admin) VALUES ('admin', ?, 1)", (admin_pwd_hash,))
        cursor.execute("INSERT OR IGNORE INTO user_settings (username, pay_mode, default_rate, currency, companies) VALUES ('admin', 'day', 700.0, '$', '預設公司')",)
    
    conn.commit()
    conn.close()

init_db()

class AuthSchema(BaseModel):
    username: str
    password: str

class WorkLogSchema(BaseModel):
    id: Optional[int] = None
    date: str
    pay_mode: str
    status: Optional[str] = "full"
    rate: float
    hours: Optional[float] = 0.0
    company: Optional[str] = "預設公司"
    location: Optional[str] = ""
    target_user: Optional[str] = None

class DeleteLogSchema(BaseModel):
    log_id: int
    target_user: Optional[str] = None

class DeleteUserSchema(BaseModel):
    target_user: str

class UserSettingsSchema(BaseModel):
    pay_mode: str
    default_rate: float
    currency: str
    companies: List[str]
    target_user: Optional[str] = None

def get_current_user_info(request: Request):
    session_token = request.cookies.get("session_token")
    if not session_token:
        raise HTTPException(status_code=401, detail="未授權")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT username, is_admin FROM users WHERE session_token = ?", (session_token,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        raise HTTPException(status_code=401, detail="無效的 Session")
    return {"username": user[0], "is_admin": bool(user[1])}

@app.post("/api/register")
def register(data: AuthSchema):
    username = data.username.strip()
    if not username or not data.password:
        raise HTTPException(status_code=400, detail="請輸入帳號與密碼")
        
    hashed_pwd = pwd_context.hash(data.password)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 0)", (username, hashed_pwd))
        cursor.execute("INSERT INTO user_settings (username, pay_mode, default_rate, currency, companies) VALUES (?, 'day', 700.0, '$', '預設公司')", (username,))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="帳號名稱已存在")
    
    conn.close()
    return {"status": "success"}

@app.post("/api/login")
def login(data: AuthSchema, response: Response):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM users WHERE username = ?", (data.username.strip(),))
    row = cursor.fetchone()
    
    if not row or not pwd_context.verify(data.password, row[0]):
        conn.close()
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    
    session_token = secrets.token_hex(32)
    cursor.execute("UPDATE users SET session_token = ? WHERE username = ?", (session_token, data.username.strip()))
    conn.commit()
    conn.close()
    
    response.set_cookie(key="session_token", value=session_token, httponly=True, max_age=31536000, samesite="lax")
    return {"status": "success"}

@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(key="session_token")
    return {"status": "success"}

@app.get("/api/me")
def get_me(user_info: dict = Depends(get_current_user_info)):
    return user_info

@app.get("/api/admin/users")
def get_all_users(user_info: dict = Depends(get_current_user_info)):
    if not user_info["is_admin"]:
        raise HTTPException(status_code=403, detail="需要管理者權限")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE is_admin = 0")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

@app.post("/api/admin/users/delete")
def delete_user(data: DeleteUserSchema, user_info: dict = Depends(get_current_user_info)):
    if not user_info["is_admin"]:
        raise HTTPException(status_code=403, detail="需要管理者權限")
    
    target = data.target_user.strip()
    if target == "admin":
        raise HTTPException(status_code=400, detail="無法刪除 Admin 帳號")
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (target,))
    cursor.execute("DELETE FROM user_settings WHERE username = ?", (target,))
    cursor.execute("DELETE FROM work_logs WHERE username = ?", (target,))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/api/settings")
def get_settings(target_user: Optional[str] = None, user_info: dict = Depends(get_current_user_info)):
    active_user = user_info["username"]
    if user_info["is_admin"] and target_user and target_user != "null":
        active_user = target_user

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT pay_mode, default_rate, currency, companies FROM user_settings WHERE username = ?", (active_user,))
    row = cursor.fetchone()
    
    if not row:
        cursor.execute("INSERT OR IGNORE INTO user_settings (username, pay_mode, default_rate, currency, companies) VALUES (?, 'day', 700.0, '$', '預設公司')", (active_user,))
        conn.commit()
        pay_mode, default_rate, currency, companies_str = 'day', 700.0, '$', '預設公司'
    else:
        pay_mode, default_rate, currency, companies_str = row

    conn.close()
    
    companies = [c.strip() for c in (companies_str or '預設公司').split(',') if c.strip()]
    if not companies:
        companies = ['預設公司']

    return {
        "pay_mode": pay_mode, 
        "default_rate": default_rate, 
        "currency": currency,
        "companies": companies
    }

@app.post("/api/settings")
def update_settings(data: UserSettingsSchema, user_info: dict = Depends(get_current_user_info)):
    active_user = user_info["username"]
    if user_info["is_admin"] and data.target_user and data.target_user != "null":
        active_user = data.target_user

    clean_companies = [c.strip() for c in data.companies if c.strip()]
    if not clean_companies:
        clean_companies = ["預設公司"]
    companies_str = ",".join(clean_companies)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO user_settings (username, pay_mode, default_rate, currency, companies)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            pay_mode = excluded.pay_mode,
            default_rate = excluded.default_rate,
            currency = excluded.currency,
            companies = excluded.companies
    """, (active_user, data.pay_mode, data.default_rate, data.currency, companies_str))
    
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/api/work/{year}/{month}")
def get_monthly_work(year: int, month: int, target_user: Optional[str] = None, user_info: dict = Depends(get_current_user_info)):
    active_user = user_info["username"]
    if user_info["is_admin"] and target_user and target_user != "null":
        active_user = target_user

    month_str = f"{year:04d}-{month:02d}"
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, date, pay_mode, status, rate, hours, daily_pay, work_days, company, location 
        FROM work_logs 
        WHERE username = ? AND date LIKE ?
        ORDER BY id ASC
    """, (active_user, f"{month_str}%"))
    rows = cursor.fetchall()
    
    logs = {}
    company_stats = {}
    
    total_hours = 0.0
    total_salary = 0.0
    
    for r in rows:
        d = r[1]
        p_mode = r[2]
        hrs = r[5] or 0.0
        w_days = r[7] or 0.0
        
        # 若為日薪模式，以每天 8 小時折算工時進行時間顯示計算
        computed_hrs = hrs if p_mode == 'hour' else w_days * 8.0
        pay = r[6] or 0.0
        comp = r[8] or "預設公司"
        
        if d not in logs:
            logs[d] = []
            
        logs[d].append({
            "id": r[0],
            "pay_mode": p_mode,
            "status": r[3],
            "rate": r[4],
            "hours": hrs,
            "daily_pay": pay,
            "work_days": w_days,
            "company": comp,
            "location": r[9] or ""
        })
        
        if comp not in company_stats:
            company_stats[comp] = {"hours": 0.0, "salary": 0.0}
            
        company_stats[comp]["hours"] += computed_hrs
        company_stats[comp]["salary"] += pay
        
        total_hours += computed_hrs
        total_salary += pay

    conn.close()
    
    return {
        "active_user": active_user,
        "logs": logs,
        "summary": {
            "total_hours": total_hours,
            "total_salary": total_salary,
            "by_company": company_stats
        }
    }

@app.post("/api/work/save")
def save_work_day(data: WorkLogSchema, user_info: dict = Depends(get_current_user_info)):
    active_user = user_info["username"]
    if user_info["is_admin"] and data.target_user and data.target_user != "null":
        active_user = data.target_user

    if data.pay_mode == 'hour':
        calculated_pay = (data.hours or 0.0) * data.rate
        work_days = round((data.hours or 0.0) / 8.0, 2)
        status_val = "hourly"
    else:
        rule = WORK_TYPES.get(data.status, WORK_TYPES["full"])
        calculated_pay = data.rate * rule["multiplier"]
        work_days = rule["days"]
        status_val = data.status

    comp = (data.company or "預設公司").strip()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if data.id:
        cursor.execute("""
            UPDATE work_logs 
            SET pay_mode = ?, status = ?, rate = ?, hours = ?, daily_pay = ?, work_days = ?, company = ?, location = ?
            WHERE id = ? AND username = ?
        """, (data.pay_mode, status_val, data.rate, data.hours, calculated_pay, work_days, comp, data.location.strip(), data.id, active_user))
    else:
        cursor.execute("""
            INSERT INTO work_logs (username, date, pay_mode, status, rate, hours, daily_pay, work_days, company, location)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (active_user, data.date, data.pay_mode, status_val, data.rate, data.hours, calculated_pay, work_days, comp, data.location.strip()))
    
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.post("/api/work/delete_item")
def delete_work_item(data: DeleteLogSchema, user_info: dict = Depends(get_current_user_info)):
    active_user = user_info["username"]
    if user_info["is_admin"] and data.target_user and data.target_user != "null":
        active_user = data.target_user

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM work_logs WHERE id = ? AND username = ?", (data.log_id, active_user))
    conn.commit()
    conn.close()
    return {"status": "success"}

app.mount("/", StaticFiles(directory="static", html=True), name="static")
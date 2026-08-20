import sqlite3
import os
import secrets
from fastapi import FastAPI, Depends, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
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
    
    # 每個使用者擁有一套獨立設定
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            username TEXT PRIMARY KEY,
            pay_mode TEXT DEFAULT 'day',
            default_rate REAL DEFAULT 700.0,
            currency TEXT DEFAULT '$'
        )
    """)
    
    # 支援單日多筆工作紀錄 (增加 primary key id)
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
            location TEXT DEFAULT ''
        )
    """)
    
    cursor.execute("SELECT username FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        admin_pwd_hash = pwd_context.hash("admin123")
        cursor.execute("INSERT INTO users (username, password_hash, is_admin) VALUES ('admin', ?, 1)", (admin_pwd_hash,))
        cursor.execute("INSERT OR IGNORE INTO user_settings (username, pay_mode, default_rate, currency) VALUES ('admin', 'day', 700.0, '$')")
    
    conn.commit()
    conn.close()

init_db()

class AuthSchema(BaseModel):
    username: str
    password: str

class WorkLogSchema(BaseModel):
    id: Optional[int] = None
    date: str
    pay_mode: str  # 'day' 或 'hour'
    status: Optional[str] = "full"  # 日薪使用的倍率類型
    rate: float                    # 單價 (日薪基礎價 或 時薪)
    hours: Optional[float] = 0.0   # 時薪使用的工時
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

# --- Auth APIs ---

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
        cursor.execute("INSERT INTO user_settings (username, pay_mode, default_rate, currency) VALUES (?, 'day', 700.0, '$')", (username,))
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

# --- Admin APIs ---

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

@app.get("/api/admin/summary/{year}/{month}")
def get_admin_summary(year: int, month: int, user_info: dict = Depends(get_current_user_info)):
    if not user_info["is_admin"]:
        raise HTTPException(status_code=403, detail="需要管理者權限")
    month_str = f"{year:04d}-{month:02d}"
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT username, SUM(work_days), SUM(daily_pay)
        FROM work_logs
        WHERE date LIKE ?
        GROUP BY username
    """, (f"{month_str}%",))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [{"username": r[0], "total_working_days": r[1] or 0.0, "total_salary": r[2] or 0.0} for r in rows]

# --- Settings & Work Data APIs ---

@app.get("/api/settings")
def get_settings(target_user: Optional[str] = None, user_info: dict = Depends(get_current_user_info)):
    active_user = user_info["username"]
    if user_info["is_admin"] and target_user and target_user != "null":
        active_user = target_user

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT pay_mode, default_rate, currency FROM user_settings WHERE username = ?", (active_user,))
    row = cursor.fetchone()
    
    if not row:
        cursor.execute("INSERT OR IGNORE INTO user_settings (username, pay_mode, default_rate, currency) VALUES (?, 'day', 700.0, '$')", (active_user,))
        conn.commit()
        pay_mode, default_rate, currency = 'day', 700.0, '$'
    else:
        pay_mode, default_rate, currency = row

    conn.close()
    return {"pay_mode": pay_mode, "default_rate": default_rate, "currency": currency}

@app.post("/api/settings")
def update_settings(data: UserSettingsSchema, user_info: dict = Depends(get_current_user_info)):
    active_user = user_info["username"]
    if user_info["is_admin"] and data.target_user and data.target_user != "null":
        active_user = data.target_user

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO user_settings (username, pay_mode, default_rate, currency)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            pay_mode = excluded.pay_mode,
            default_rate = excluded.default_rate,
            currency = excluded.currency
    """, (active_user, data.pay_mode, data.default_rate, data.currency))
    
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
        SELECT id, date, pay_mode, status, rate, hours, daily_pay, work_days, location 
        FROM work_logs 
        WHERE username = ? AND date LIKE ?
        ORDER BY id ASC
    """, (active_user, f"{month_str}%"))
    rows = cursor.fetchall()
    
    logs = {}
    for r in rows:
        d = r[1]
        if d not in logs:
            logs[d] = []
        logs[d].append({
            "id": r[0],
            "pay_mode": r[2],
            "status": r[3],
            "rate": r[4],
            "hours": r[5],
            "daily_pay": r[6],
            "work_days": r[7],
            "location": r[8] or ""
        })
    
    cursor.execute("""
        SELECT SUM(work_days), SUM(daily_pay)
        FROM work_logs
        WHERE username = ? AND date LIKE ?
    """, (active_user, f"{month_str}%"))
    
    total_days, total_salary = cursor.fetchone()
    conn.close()
    
    return {
        "active_user": active_user,
        "logs": logs,
        "summary": {
            "total_working_days": total_days or 0.0,
            "total_salary": total_salary or 0.0
        }
    }

@app.post("/api/work/save")
def save_work_day(data: WorkLogSchema, user_info: dict = Depends(get_current_user_info)):
    active_user = user_info["username"]
    if user_info["is_admin"] and data.target_user and data.target_user != "null":
        active_user = data.target_user

    # 計算薪資與工作日數
    if data.pay_mode == 'hour':
        calculated_pay = (data.hours or 0.0) * data.rate
        work_days = round((data.hours or 0.0) / 8.0, 2)  # 以 8 小時為 1 工作日算折合天數
        status_val = "hourly"
    else:
        rule = WORK_TYPES.get(data.status, WORK_TYPES["full"])
        calculated_pay = data.rate * rule["multiplier"]
        work_days = rule["days"]
        status_val = data.status

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if data.id:
        # 修改既有紀錄
        cursor.execute("""
            UPDATE work_logs 
            SET pay_mode = ?, status = ?, rate = ?, hours = ?, daily_pay = ?, work_days = ?, location = ?
            WHERE id = ? AND username = ?
        """, (data.pay_mode, status_val, data.rate, data.hours, calculated_pay, work_days, data.location.strip(), data.id, active_user))
    else:
        # 新增一筆紀錄 (支援一日多筆工作)
        cursor.execute("""
            INSERT INTO work_logs (username, date, pay_mode, status, rate, hours, daily_pay, work_days, location)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (active_user, data.date, data.pay_mode, status_val, data.rate, data.hours, calculated_pay, work_days, data.location.strip()))
    
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
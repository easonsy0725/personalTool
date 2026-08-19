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
    "off": {"days": 0.0, "multiplier": 0.0, "label": "Off"},
    "half": {"days": 0.5, "multiplier": 0.5, "label": "Half Day"},
    "full": {"days": 1.0, "multiplier": 1.0, "label": "Full Day"},
    "ot_1.5": {"days": 1.5, "multiplier": 1.5, "label": "OT Standard"},
    "ot_2.0": {"days": 2.0, "multiplier": 2.0, "label": "OT Past 00:00"},
    "ot_3.0": {"days": 3.0, "multiplier": 3.0, "label": "OT Past 06:00"},
}

def init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 使用者資料表 (記錄帳號密碼與裝置憑證)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            session_token TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS work_logs (
            date TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            daily_pay REAL NOT NULL,
            work_days REAL NOT NULL DEFAULT 1.0,
            location TEXT DEFAULT ''
        )
    """)
    
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('default_daily_rate', '700')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('currency', '$')")
    
    conn.commit()
    conn.close()

init_db()

class AuthSchema(BaseModel):
    username: str
    password: str

class WorkLogSchema(BaseModel):
    date: str
    status: str
    location: Optional[str] = ""

class SettingsSchema(BaseModel):
    default_daily_rate: float
    currency: str

# 驗證 Session Cookie (保持登入與記住裝置的核心)
def get_current_user(request: Request):
    session_token = request.cookies.get("session_token")
    if not session_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE session_token = ?", (session_token,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid Session")
    return user[0]

# --- 身份驗證 API ---

@app.post("/api/register")
def register(data: AuthSchema):
    username = data.username.strip()
    if not username or not data.password:
        raise HTTPException(status_code=400, detail="請填寫帳號與密碼")
        
    hashed_pwd = pwd_context.hash(data.password)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, hashed_pwd))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="帳號已被註冊")
    
    conn.close()
    return {"status": "success", "message": "註冊成功"}

@app.post("/api/login")
def login(data: AuthSchema, response: Response):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM users WHERE username = ?", (data.username.strip(),))
    row = cursor.fetchone()
    
    if not row or not pwd_context.verify(data.password, row[0]):
        conn.close()
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    
    # 產生 Session Token 並寫入資料庫
    session_token = secrets.token_hex(32)
    cursor.execute("UPDATE users SET session_token = ? WHERE username = ?", (session_token, data.username.strip()))
    conn.commit()
    conn.close()
    
    # 設定可保持 1 年 (365 天) 登入的強效 Cookie
    response.set_cookie(
        key="session_token", 
        value=session_token, 
        httponly=True, 
        max_age=31536000,
        samesite="lax"
    )
    return {"status": "success"}

@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(key="session_token")
    return {"status": "success"}

# --- 受保護的數據 API ---

@app.get("/api/settings")
def get_settings(user: str = Depends(get_current_user)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM settings")
    settings = dict(cursor.fetchall())
    conn.close()
    return settings

@app.post("/api/settings")
def update_settings(data: SettingsSchema, user: str = Depends(get_current_user)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE settings SET value = ? WHERE key = 'default_daily_rate'", (str(data.default_daily_rate),))
    cursor.execute("UPDATE settings SET value = ? WHERE key = 'currency'", (data.currency,))
    
    cursor.execute("SELECT date, status FROM work_logs WHERE status != 'off'")
    rows = cursor.fetchall()
    
    for d_date, d_status in rows:
        rule = WORK_TYPES.get(d_status, WORK_TYPES["off"])
        new_pay = data.default_daily_rate * rule["multiplier"]
        cursor.execute("UPDATE work_logs SET daily_pay = ? WHERE date = ?", (new_pay, d_date))

    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/api/work/{year}/{month}")
def get_monthly_work(year: int, month: int, user: str = Depends(get_current_user)):
    month_str = f"{year:04d}-{month:02d}"
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT date, status, daily_pay, work_days, location FROM work_logs WHERE date LIKE ?", 
        (f"{month_str}%",)
    )
    rows = cursor.fetchall()
    
    logs = {
        r[0]: {
            "status": r[1],
            "daily_pay": r[2],
            "work_days": r[3],
            "location": r[4] if r[4] else ""
        } 
        for r in rows
    }
    
    cursor.execute("""
        SELECT SUM(work_days), SUM(daily_pay)
        FROM work_logs
        WHERE date LIKE ? AND status != 'off'
    """, (f"{month_str}%",))
    
    total_days, total_salary = cursor.fetchone()
    conn.close()
    
    return {
        "logs": logs,
        "summary": {
            "total_working_days": total_days or 0.0,
            "total_salary": total_salary or 0.0
        }
    }

@app.post("/api/work/update")
def update_work_day(data: WorkLogSchema, user: str = Depends(get_current_user)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT value FROM settings WHERE key = 'default_daily_rate'")
    row = cursor.fetchone()
    base_rate = float(row[0]) if row else 700.0
    
    rule = WORK_TYPES.get(data.status, WORK_TYPES["off"])
    
    if data.status == "off":
        cursor.execute("DELETE FROM work_logs WHERE date = ?", (data.date,))
    else:
        calculated_pay = base_rate * rule["multiplier"]
        work_days = rule["days"]
        location_val = data.location.strip() if data.location else ""
        
        cursor.execute("""
            INSERT INTO work_logs (date, status, daily_pay, work_days, location)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                status = excluded.status,
                daily_pay = excluded.daily_pay,
                work_days = excluded.work_days,
                location = excluded.location
        """, (data.date, data.status, calculated_pay, work_days, location_val))
    
    conn.commit()
    conn.close()
    return {"status": "success"}

# 掛載 static 資料夾
app.mount("/", StaticFiles(directory="static", html=True), name="static")
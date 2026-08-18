import sqlite3
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Work & Salary Tracker")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = "data/dashboard.db"

WORK_TYPES = {
    "off": {"days": 0.0, "multiplier": 0.0, "label": "Off"},
    "half": {"days": 0.5, "multiplier": 0.5, "label": "Half Day"},
    "full": {"days": 1.0, "multiplier": 1.0, "label": "Full Day"},
    "ot_1.5": {"days": 1.5, "multiplier": 1.5, "label": "OT (1.5x)"},
    "ot_2.0": {"days": 2.0, "multiplier": 2.0, "label": "OT Past 00:00 (2x)"},
    "ot_3.0": {"days": 3.0, "multiplier": 3.0, "label": "OT Past 06:00 (3x)"},
}

def init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
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
    
    cursor.execute("PRAGMA table_info(work_logs)")
    columns = [column[1] for column in cursor.fetchall()]
    if "work_days" not in columns:
        cursor.execute("ALTER TABLE work_logs ADD COLUMN work_days REAL DEFAULT 1.0")
    if "location" not in columns:
        cursor.execute("ALTER TABLE work_logs ADD COLUMN location TEXT DEFAULT ''")

    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('default_daily_rate', '1500')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('currency', '$')")
    
    conn.commit()
    conn.close()

init_db()

class WorkLogSchema(BaseModel):
    date: str
    status: str
    location: Optional[str] = ""

class SettingsSchema(BaseModel):
    default_daily_rate: float
    currency: str

@app.get("/api/settings")
def get_settings():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM settings")
    settings = dict(cursor.fetchall())
    conn.close()
    return settings

@app.post("/api/settings")
def update_settings(data: SettingsSchema):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. 更新基本設定
    cursor.execute("UPDATE settings SET value = ? WHERE key = 'default_daily_rate'", (str(data.default_daily_rate),))
    cursor.execute("UPDATE settings SET value = ? WHERE key = 'currency'", (data.currency,))
    
    # 2. 自動按新基本日薪重新計算所有舊紀錄的金額
    cursor.execute("SELECT date, status FROM work_logs WHERE status != 'off'")
    rows = cursor.fetchall()
    
    for row in rows:
        d_date, d_status = row[0], row[1]
        rule = WORK_TYPES.get(d_status, WORK_TYPES["off"])
        new_pay = data.default_daily_rate * rule["multiplier"]
        cursor.execute("UPDATE work_logs SET daily_pay = ? WHERE date = ?", (new_pay, d_date))

    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/api/work/{year}/{month}")
def get_monthly_work(year: int, month: int):
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
def update_work_day(data: WorkLogSchema):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 取得最新設定的日薪費率
    cursor.execute("SELECT value FROM settings WHERE key = 'default_daily_rate'")
    row = cursor.fetchone()
    base_rate = float(row[0]) if row else 1500.0
    
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

app.mount("/", StaticFiles(directory="static", html=True), name="static")
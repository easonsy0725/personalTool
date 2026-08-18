import sqlite3
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Daily Command Center")

# 允許跨網域請求 (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = "data/dashboard.db"

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
            note TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL,
            is_completed BOOLEAN DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT
        )
    """)
    # 初始化預設日薪
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('default_daily_rate', '1500')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('currency', '$')")
    # 初始化單一筆記紀錄
    cursor.execute("INSERT OR IGNORE INTO notes (id, content) VALUES (1, '')")
    conn.commit()
    conn.close()

init_db()

# --- Pydantic 模型 ---
class WorkLogSchema(BaseModel):
    date: str
    status: str  # 'worked' 或 'off'
    daily_pay: Optional[float] = None
    note: Optional[str] = ""

class TodoSchema(BaseModel):
    task: str

class SettingsSchema(BaseModel):
    default_daily_rate: float
    currency: str

class NoteSchema(BaseModel):
    content: str

# --- API 路由 ---

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
    cursor.execute("UPDATE settings SET value = ? WHERE key = 'default_daily_rate'", (str(data.default_daily_rate),))
    cursor.execute("UPDATE settings SET value = ? WHERE key = 'currency'", (data.currency,))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/api/work/{year}/{month}")
def get_monthly_work(year: int, month: int):
    month_str = f"{year:04d}-{month:02d}"
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT date, status, daily_pay, note FROM work_logs WHERE date LIKE ?", (f"{month_str}%",))
    rows = cursor.fetchall()
    
    logs = {r[0]: {"status": r[1], "daily_pay": r[2], "note": r[3]} for r in rows}
    
    # 計算當月統計
    cursor.execute("""
        SELECT COUNT(*), SUM(daily_pay)
        FROM work_logs
        WHERE date LIKE ? AND status = 'worked'
    """, (f"{month_str}%",))
    
    total_days, total_salary = cursor.fetchone()
    conn.close()
    
    return {
        "logs": logs,
        "summary": {
            "total_working_days": total_days or 0,
            "total_salary": total_salary or 0.0
        }
    }

@app.post("/api/work/toggle")
def toggle_work_day(data: WorkLogSchema):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 取得預設日薪
    if data.daily_pay is None:
        cursor.execute("SELECT value FROM settings WHERE key = 'default_daily_rate'")
        data.daily_pay = float(cursor.fetchone()[0])
        
    cursor.execute("""
        INSERT INTO work_logs (date, status, daily_pay, note)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            status = excluded.status,
            daily_pay = excluded.daily_pay,
            note = excluded.note
    """, (data.date, data.status, data.daily_pay, data.note))
    
    conn.commit()
    conn.close()
    return {"status": "success"}

# 待辦事項 API
@app.get("/api/todos")
def get_todos():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, task, is_completed FROM todos")
    todos = [{"id": r[0], "task": r[1], "is_completed": bool(r[2])} for r in cursor.fetchall()]
    conn.close()
    return todos

@app.post("/api/todos")
def add_todo(data: TodoSchema):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO todos (task) VALUES (?)", (data.task,))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.put("/api/todos/{todo_id}/toggle")
def toggle_todo(todo_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE todos SET is_completed = NOT is_completed WHERE id = ?", (todo_id,))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.delete("/api/todos/{todo_id}")
def delete_todo(todo_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
    conn.commit()
    conn.close()
    return {"status": "success"}

# 筆記 API
@app.get("/api/note")
def get_note():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT content FROM notes WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    return {"content": row[0] if row else ""}

@app.post("/api/note")
def update_note(data: NoteSchema):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE notes SET content = ? WHERE id = 1", (data.content,))
    conn.commit()
    conn.close()
    return {"status": "success"}

# 靜態頁面服務
app.mount("/", StaticFiles(directory="static", html=True), name="static")
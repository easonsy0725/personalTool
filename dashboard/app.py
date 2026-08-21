import os
import sqlite3
import json
from fastapi import FastAPI, Request, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from passlib.hash import pbkdf2_sha256

app = FastAPI()

# 靜態檔案掛載
app.mount("/static", StaticFiles(directory="static"), name="static")

DB_FILE = "data/dashboard.db"

def init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_settings (
            username TEXT PRIMARY KEY,
            pay_mode TEXT DEFAULT 'day',
            default_rate REAL DEFAULT 700,
            currency TEXT DEFAULT '$',
            companies TEXT DEFAULT '["預設公司"]'
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS work_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            company TEXT DEFAULT '預設公司',
            date TEXT NOT NULL,
            pay_mode TEXT DEFAULT 'day',
            status TEXT,
            rate REAL,
            hours REAL,
            daily_pay REAL,
            location TEXT
        )
    ''')
    
    # 檢查並補齊 missing 欄位
    c.execute("PRAGMA table_info(work_logs)")
    columns = [col[1] for col in c.fetchall()]
    if 'company' not in columns:
        c.execute("ALTER TABLE work_logs ADD COLUMN company TEXT DEFAULT '預設公司'")

    c.execute("PRAGMA table_info(user_settings)")
    settings_cols = [col[1] for col in c.fetchall()]
    if 'companies' not in settings_cols:
        c.execute("ALTER TABLE user_settings ADD COLUMN companies TEXT DEFAULT '[\"預設公司\"]'")

    # 初始化預設管理者
    c.execute("SELECT * FROM users WHERE username='eason'")
    if not c.fetchone():
        hashed_pwd = pbkdf2_sha256.hash("eason")
        c.execute("INSERT INTO users (username, password, is_admin) VALUES (?, ?, 1)", ("eason", hashed_pwd))
        c.execute("INSERT OR IGNORE INTO user_settings (username, pay_mode, default_rate, currency, companies) VALUES (?, 'day', 700, '$', '[\"預設公司\"]')", ("eason",))

    conn.commit()
    conn.close()

init_db()

def get_current_user_from_cookie(request: Request):
    user = request.cookies.get("username")
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user

def check_permission(request: Request, target_user: str):
    user = get_current_user_from_cookie(request)
    is_admin = request.cookies.get("is_admin") == "1"
    if user == target_user or is_admin:
        return user
    raise HTTPException(status_code=403, detail="Forbidden")

@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    user = request.cookies.get("username")
    if not user:
        return FileResponse('static/login.html')
    return FileResponse('static/index.html')

@app.get("/manifest.json")
async def serve_manifest():
    return FileResponse('static/manifest.json')

@app.post("/api/login")
async def login(request: Request):
    data = await request.json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT password, is_admin FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()

    if row and pbkdf2_sha256.verify(password, row[0]):
        response = JSONResponse({"success": True, "username": username, "is_admin": row[1]})
        response.set_cookie(key="username", value=username, httponly=True)
        response.set_cookie(key="is_admin", value=str(row[1]), httponly=True)
        return response

    raise HTTPException(status_code=401, detail="帳號或密碼錯誤")

@app.post("/api/logout")
async def logout():
    response = JSONResponse({"success": True})
    response.delete_cookie("username")
    response.delete_cookie("is_admin")
    return response

@app.get("/api/me")
async def me(request: Request):
    username = get_current_user_from_cookie(request)
    is_admin = request.cookies.get("is_admin") == "1"
    return {"username": username, "is_admin": is_admin}

@app.get("/api/admin/users")
async def list_users(request: Request):
    if request.cookies.get("is_admin") != "1":
        raise HTTPException(status_code=403, detail="Forbidden")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT username FROM users")
    users = [row[0] for row in c.fetchall()]
    conn.close()
    return users

@app.post("/api/admin/users/delete")
async def delete_user(request: Request):
    if request.cookies.get("is_admin") != "1":
        raise HTTPException(status_code=403, detail="Forbidden")
    data = await request.json()
    target_user = data.get('target_user')
    if target_user == request.cookies.get("username"):
        raise HTTPException(status_code=400, detail="Cannot delete current logged in admin")

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE username=?", (target_user,))
    c.execute("DELETE FROM user_settings WHERE username=?", (target_user,))
    c.execute("DELETE FROM work_logs WHERE username=?", (target_user,))
    conn.commit()
    conn.close()
    return {"success": True}

@app.post("/api/register")
async def register(request: Request):
    data = await request.json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        raise HTTPException(status_code=400, detail="請輸入帳號和密碼")

    hashed_pwd = pbkdf2_sha256.hash(password)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, password, is_admin) VALUES (?, ?, 0)", (username, hashed_pwd))
        c.execute("INSERT INTO user_settings (username, pay_mode, default_rate, currency, companies) VALUES (?, 'day', 700, '$', '[\"預設公司\"]')", (username,))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="帳號名稱已存在")
    conn.close()
    return {"success": True}

@app.get("/api/settings")
async def get_settings(request: Request, target_user: str = None):
    current_user = get_current_user_from_cookie(request)
    target = target_user or current_user
    check_permission(request, target)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT pay_mode, default_rate, currency, companies FROM user_settings WHERE username=?", (target,))
    row = c.fetchone()
    conn.close()

    if row:
        try:
            companies = json.loads(row[3]) if row[3] else ["預設公司"]
        except:
            companies = ["預設公司"]
        return {
            "pay_mode": row[0],
            "default_rate": row[1],
            "currency": row[2],
            "companies": companies
        }
    return {
        "pay_mode": "day",
        "default_rate": 700,
        "currency": "$",
        "companies": ["預設公司"]
    }

@app.post("/api/settings")
async def save_settings(request: Request):
    data = await request.json()
    target_user = data.get('target_user') or get_current_user_from_cookie(request)
    check_permission(request, target_user)

    pay_mode = data.get('pay_mode', 'day')
    default_rate = float(data.get('default_rate', 700))
    currency = data.get('currency', '$')
    companies = data.get('companies', ['預設公司'])

    companies = [c.strip() for c in companies if c and c.strip()]
    if not companies:
        companies = ['預設公司']

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT companies FROM user_settings WHERE username=?", (target_user,))
    old_row = c.fetchone()
    old_companies = []
    if old_row and old_row[0]:
        try:
            old_companies = json.loads(old_row[0])
        except:
            pass

    # 自動更換預設公司與移轉舊紀錄邏輯
    if "預設公司" in old_companies and "預設公司" not in companies:
        first_new_company = companies[0]
        c.execute("UPDATE work_logs SET company=? WHERE username=? AND (company='預設公司' OR company IS NULL OR company='')", (first_new_company, target_user))

    companies_json = json.dumps(companies, ensure_ascii=False)
    c.execute('''
        INSERT INTO user_settings (username, pay_mode, default_rate, currency, companies)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            pay_mode=excluded.pay_mode,
            default_rate=excluded.default_rate,
            currency=excluded.currency,
            companies=excluded.companies
    ''', (target_user, pay_mode, default_rate, currency, companies_json))

    conn.commit()
    conn.close()
    return {"success": True}

@app.get("/api/work/{year}/{month}")
async def get_work_logs(year: int, month: int, request: Request, target_user: str = None):
    current_user = get_current_user_from_cookie(request)
    target = target_user or current_user
    check_permission(request, target)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    prefix = f"{year}-{month:02d}-%"
    c.execute('''
        SELECT id, date, company, pay_mode, status, rate, hours, daily_pay, location 
        FROM work_logs 
        WHERE username=? AND date LIKE ?
        ORDER BY date ASC, id ASC
    ''', (target, prefix))
    
    rows = c.fetchall()
    conn.close()

    logs = {}
    total_hours = 0.0
    total_salary = 0.0
    by_company = {}

    for row in rows:
        lid, date, company, pay_mode, status, rate, hours, daily_pay, location = row
        company_name = company if company else '預設公司'

        item = {
            "id": lid,
            "date": date,
            "company": company_name,
            "pay_mode": pay_mode,
            "status": status,
            "rate": rate,
            "hours": hours,
            "daily_pay": daily_pay,
            "location": location or ""
        }
        
        if date not in logs:
            logs[date] = []
        logs[date].append(item)

        total_hours += hours
        total_salary += daily_pay

        if company_name not in by_company:
            by_company[company_name] = {"hours": 0.0, "salary": 0.0}
        by_company[company_name]["hours"] += hours
        by_company[company_name]["salary"] += daily_pay

    return {
        "logs": logs,
        "summary": {
            "total_hours": round(total_hours, 2),
            "total_salary": round(total_salary, 2),
            "by_company": by_company
        }
    }

@app.post("/api/work/save")
async def save_work_log(request: Request):
    data = await request.json()
    target_user = data.get('target_user') or get_current_user_from_cookie(request)
    check_permission(request, target_user)

    log_id = data.get('id')
    date = data.get('date')
    company = data.get('company', '預設公司').strip() or '預設公司'
    pay_mode = data.get('pay_mode', 'day')
    status = data.get('status', 'full')
    rate = float(data.get('rate', 0))
    hours = float(data.get('hours', 0))
    location = data.get('location', '').strip()

    daily_pay = 0.0
    if pay_mode == 'day':
        hours = 8.0
        multipliers = {
            'half': 0.5,
            'full': 1.0,
            'ot_1.5': 1.5,
            'ot_2.0': 2.0,
            'ot_3.0': 3.0
        }
        mult = multipliers.get(status, 1.0)
        daily_pay = rate * mult
        if status == 'half':
            hours = 4.0
    else:
        status = 'hourly'
        daily_pay = rate * hours

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    if log_id:
        c.execute('''
            UPDATE work_logs 
            SET company=?, pay_mode=?, status=?, rate=?, hours=?, daily_pay=?, location=?
            WHERE id=? AND username=?
        ''', (company, pay_mode, status, rate, hours, daily_pay, location, log_id, target_user))
    else:
        c.execute('''
            INSERT INTO work_logs (username, company, date, pay_mode, status, rate, hours, daily_pay, location)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (target_user, company, date, pay_mode, status, rate, hours, daily_pay, location))

    conn.commit()
    conn.close()
    return {"success": True}

@app.post("/api/work/delete_item")
async def delete_work_item(request: Request):
    data = await request.json()
    target_user = data.get('target_user') or get_current_user_from_cookie(request)
    check_permission(request, target_user)

    log_id = data.get('log_id')

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM work_logs WHERE id=? AND username=?", (log_id, target_user))
    conn.commit()
    conn.close()
    return {"success": True}
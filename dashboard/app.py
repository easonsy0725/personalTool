import os
import sqlite3
import json
from flask import Flask, request, jsonify, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder='static')
app.secret_key = 'super_secret_key_change_me_in_production'
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False

DB_FILE = "data/dashboard.db"

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

def init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # 1. 使用者資料表：新增 role (employee, accounting, admin) 與 company 欄位
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT,
            is_admin INTEGER DEFAULT 0,
            role TEXT DEFAULT 'employee',
            company TEXT DEFAULT ''
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
    
    # 員工原始工作紀錄
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

    # 2. 獨立會計資料庫表：會計專屬修改沙盒，不覆蓋員工原始紀錄
    c.execute('''
        CREATE TABLE IF NOT EXISTS accounting_work_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_log_id INTEGER,
            username TEXT NOT NULL,
            company TEXT NOT NULL,
            date TEXT NOT NULL,
            pay_mode TEXT DEFAULT 'day',
            status TEXT,
            rate REAL,
            hours REAL,
            daily_pay REAL,
            location TEXT,
            notes TEXT
        )
    ''')
    
    # 自動補齊舊版 SQLite 欄位
    c.execute("PRAGMA table_info(users)")
    user_cols = [col[1] for col in c.fetchall()]
    if 'role' not in user_cols:
        c.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'employee'")
    if 'company' not in user_cols:
        c.execute("ALTER TABLE users ADD COLUMN company TEXT DEFAULT ''")

    # 重置預設管理員帳號
    c.execute("DELETE FROM users WHERE username='admin'")
    hashed_pwd = generate_password_hash("admin")
    c.execute("INSERT INTO users (username, password, is_admin, role) VALUES ('admin', ?, 1, 'admin')", (hashed_pwd,))
    
    conn.commit()
    conn.close()

init_db()

def get_current_user():
    return session.get('username')

def get_user_role():
    return session.get('role', 'employee')

def get_user_company():
    return session.get('company', '')

def is_admin():
    return session.get('is_admin', 0) == 1 or get_user_role() == 'admin'

@app.route('/')
def serve_index():
    user = get_current_user()
    if not user:
        return send_from_directory('static', 'login.html')
    
    # 會計人員自動重定向至會計專屬介面
    if get_user_role() == 'accounting':
        return send_from_directory('static', 'accounting.html')
    
    return send_from_directory('static', 'index.html')

@app.route('/login.html')
def serve_login():
    return send_from_directory('static', 'login.html')

@app.route('/accounting.html')
def serve_accounting():
    if get_user_role() not in ['accounting', 'admin']:
        return send_from_directory('static', 'login.html')
    return send_from_directory('static', 'accounting.html')

@app.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS':
        return jsonify({"success": True}), 200

    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    if not username or not password:
        return jsonify({"success": False, "message": "請輸入帳號與密碼"}), 400

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT password, is_admin, role, company FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()

    if not row or not check_password_hash(row[0], password):
        return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401

    session['username'] = username
    session['is_admin'] = row[1]
    session['role'] = row[2] or 'employee'
    session['company'] = row[3] or ''

    return jsonify({
        "success": True, 
        "username": username, 
        "is_admin": row[1],
        "role": session['role'],
        "company": session['company']
    })

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True})

@app.route('/api/me', methods=['GET'])
def me():
    username = get_current_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({
        "username": username, 
        "is_admin": is_admin(),
        "role": get_user_role(),
        "company": get_user_company()
    })

# --- 會計模式專屬 API (沙盒區域) ---

@app.route('/api/accounting/work/<int:year>/<int:month>', methods=['GET'])
def get_accounting_work_logs(year, month):
    role = get_user_role()
    if role not in ['accounting', 'admin']:
        return jsonify({"error": "Forbidden: Accounting Mode Only"}), 403

    acct_company = get_user_company()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    prefix = f"{year}-{month:02d}-%"

    # 1. 自動從原始員工資料同步/建立會計區數據 (若未建立)
    if role == 'admin':
        c.execute('SELECT id, username, company, date, pay_mode, status, rate, hours, daily_pay, location FROM work_logs WHERE date LIKE ?', (prefix,))
    else:
        c.execute('SELECT id, username, company, date, pay_mode, status, rate, hours, daily_pay, location FROM work_logs WHERE company=? AND date LIKE ?', (acct_company, prefix))
    
    raw_logs = c.fetchall()

    for r in raw_logs:
        orig_id, un, comp, dt, pm, st, rt, hr, dp, loc = r
        c.execute('SELECT id FROM accounting_work_logs WHERE original_log_id=?', (orig_id,))
        if not c.fetchone():
            c.execute('''
                INSERT INTO accounting_work_logs 
                (original_log_id, username, company, date, pay_mode, status, rate, hours, daily_pay, location)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (orig_id, un, comp, dt, pm, st, rt, hr, dp, loc))
    conn.commit()

    # 2. 僅載入該會計所屬公司的獨立資料
    if role == 'admin':
        c.execute('SELECT id, original_log_id, username, company, date, pay_mode, status, rate, hours, daily_pay, location, notes FROM accounting_work_logs WHERE date LIKE ? ORDER BY date ASC', (prefix,))
    else:
        c.execute('SELECT id, original_log_id, username, company, date, pay_mode, status, rate, hours, daily_pay, location, notes FROM accounting_work_logs WHERE company=? AND date LIKE ? ORDER BY date ASC', (acct_company, prefix))

    rows = c.fetchall()
    conn.close()

    logs = []
    total_salary = 0.0
    for r in rows:
        total_salary += r[9]
        logs.append({
            "id": r[0],
            "original_log_id": r[1],
            "username": r[2],
            "company": r[3],
            "date": r[4],
            "pay_mode": r[5],
            "status": r[6],
            "rate": r[7],
            "hours": r[8],
            "daily_pay": r[9],
            "location": r[10] or "",
            "notes": r[11] or ""
        })

    return jsonify({
        "company": acct_company if role != 'admin' else "全公司 (管理者權限)",
        "logs": logs,
        "total_salary": round(total_salary, 2)
    })

@app.route('/api/accounting/work/save', methods=['POST'])
def save_accounting_work_log():
    role = get_user_role()
    if role not in ['accounting', 'admin']:
        return jsonify({"error": "Forbidden"}), 403

    acct_company = get_user_company()
    data = request.json or {}
    log_id = data.get('id')
    rate = float(data.get('rate', 0))
    hours = float(data.get('hours', 0))
    notes = data.get('notes', '').strip()
    
    pay_mode = data.get('pay_mode', 'day')
    status = data.get('status', 'full')

    # 計算薪資
    daily_pay = 0.0
    if pay_mode == 'day':
        multipliers = {'half': 0.5, 'full': 1.0, 'ot_1.5': 1.5, 'ot_2.0': 2.0, 'ot_3.0': 3.0}
        daily_pay = rate * multipliers.get(status, 1.0)
    else:
        daily_pay = rate * hours

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # 安全檢查：限制會計只能修改自己公司的紀錄
    if role != 'admin':
        c.execute('SELECT company FROM accounting_work_logs WHERE id=?', (log_id,))
        row = c.fetchone()
        if not row or row[0] != acct_company:
            conn.close()
            return jsonify({"error": "Forbidden: Target log does not belong to your company"}), 403

    c.execute('''
        UPDATE accounting_work_logs 
        SET rate=?, hours=?, daily_pay=?, status=?, notes=?
        WHERE id=?
    ''', (rate, hours, daily_pay, status, notes, log_id))

    conn.commit()
    conn.close()
    return jsonify({"success": True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
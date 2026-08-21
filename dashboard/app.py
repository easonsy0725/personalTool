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
    
    # 1. 使用者資料表：支援 display_name, role, company
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT,
            display_name TEXT DEFAULT '',
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
    
    # 員工原始工作紀錄表
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

    # 獨立會計資料庫表：沙盒機制
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
    if 'display_name' not in user_cols:
        c.execute("ALTER TABLE users ADD COLUMN display_name TEXT DEFAULT ''")

    c.execute("PRAGMA table_info(work_logs)")
    work_cols = [col[1] for col in c.fetchall()]
    if 'company' not in work_cols:
        c.execute("ALTER TABLE work_logs ADD COLUMN company TEXT DEFAULT '預設公司'")

    c.execute("PRAGMA table_info(user_settings)")
    settings_cols = [col[1] for col in c.fetchall()]
    if 'companies' not in settings_cols:
        c.execute("ALTER TABLE user_settings ADD COLUMN companies TEXT DEFAULT '[\"預設公司\"]'")

    # 安全的預設 admin 初始化：保護既有資料
    c.execute("SELECT username FROM users WHERE username='admin'")
    if not c.fetchone():
        hashed_pwd = generate_password_hash("admin")
        c.execute("INSERT INTO users (username, password, display_name, is_admin, role) VALUES ('admin', ?, '系統管理者', 1, 'admin')", (hashed_pwd,))
        c.execute("INSERT INTO user_settings (username, pay_mode, default_rate, currency, companies) VALUES ('admin', 'day', 700, '$', '[\"預設公司\"]')")

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

def check_permission(target_user):
    user = get_current_user()
    if not user:
        return False
    if user == target_user or is_admin():
        return True
    return False

@app.route('/')
def serve_index():
    user = get_current_user()
    if not user:
        return send_from_directory('static', 'login.html')
    
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

@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json')

@app.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS':
        return jsonify({"success": True}), 200

    try:
        data = request.json or {}
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not username or not password:
            return jsonify({"success": False, "message": "請輸入帳號與密碼"}), 400

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT password, is_admin, role, company, display_name FROM users WHERE username=?", (username,))
        row = c.fetchone()
        conn.close()

        if not row or not row[0]:
            return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401

        if check_password_hash(row[0], password):
            session['username'] = username
            session['is_admin'] = row[1]
            session['role'] = row[2] or 'employee'
            session['company'] = row[3] or ''
            session['display_name'] = row[4] or username
            return jsonify({
                "success": True, 
                "username": username, 
                "display_name": session['display_name'],
                "is_admin": row[1],
                "role": session['role'],
                "company": session['company']
            })
        
        return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401

    except Exception as e:
        return jsonify({"success": False, "message": f"Server Error: {str(e)}"}), 500

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
        "display_name": session.get('display_name', username),
        "is_admin": is_admin(),
        "role": get_user_role(),
        "company": get_user_company()
    })

# 管理者：取得所有使用者詳細清單
@app.route('/api/admin/users', methods=['GET'])
def list_users():
    if not is_admin():
        return jsonify({"error": "Forbidden"}), 403
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT username, display_name, role, company, is_admin FROM users")
    
    users = []
    for row in c.fetchall():
        users.append({
            "username": row[0],
            "display_name": row[1] or row[0],
            "role": row[2] or 'employee',
            "company": row[3] or '',
            "is_admin": row[4]
        })
    conn.close()
    return jsonify(users)

# 管理者：更新任一使用者的 Display Name / Role / Company
@app.route('/api/admin/users/update', methods=['POST'])
def update_user_by_admin():
    if not is_admin():
        return jsonify({"error": "Forbidden"}), 403

    data = request.json or {}
    target_user = data.get('target_user', '').strip()
    new_display_name = data.get('display_name', '').strip()
    new_role = data.get('role', 'employee').strip()
    new_company = data.get('company', '').strip()

    if not target_user:
        return jsonify({"error": "必須指定目標使用者"}), 400

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''
        UPDATE users 
        SET display_name=?, role=?, company=?
        WHERE username=?
    ''', (new_display_name or target_user, new_role, new_company, target_user))

    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/admin/users/delete', methods=['POST'])
def delete_user():
    if not is_admin():
        return jsonify({"error": "Forbidden"}), 403
    data = request.json or {}
    target_user = data.get('target_user')
    if target_user == get_current_user():
        return jsonify({"error": "無法刪除當前登入的管理者帳號"}), 400

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE username=?", (target_user,))
    c.execute("DELETE FROM user_settings WHERE username=?", (target_user,))
    c.execute("DELETE FROM work_logs WHERE username=?", (target_user,))
    c.execute("DELETE FROM accounting_work_logs WHERE username=?", (target_user,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    display_name = data.get('display_name', '').strip() or username
    role = data.get('role', 'employee').strip()
    company = data.get('company', '').strip()

    if not username or not password:
        return jsonify({"error": "請輸入帳號和密碼"}), 400

    hashed_pwd = generate_password_hash(password)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, password, display_name, is_admin, role, company) VALUES (?, ?, ?, 0, ?, ?)", 
                  (username, hashed_pwd, display_name, role, company))
        c.execute("INSERT INTO user_settings (username, pay_mode, default_rate, currency, companies) VALUES (?, 'day', 700, '$', ?)", 
                  (username, json.dumps([company] if company else ["預設公司"])))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "帳號名稱已存在"}), 400
    conn.close()
    return jsonify({"success": True})

@app.route('/api/settings', methods=['GET', 'POST'])
def settings():
    if not get_current_user():
        return jsonify({"error": "Unauthorized"}), 401

    if request.method == 'GET':
        target_user = request.args.get('target_user', get_current_user())
        if not check_permission(target_user):
            return jsonify({"error": "Forbidden"}), 403

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT pay_mode, default_rate, currency, companies FROM user_settings WHERE username=?", (target_user,))
        row = c.fetchone()
        conn.close()

        if row:
            try:
                companies = json.loads(row[3]) if row[3] else ["預設公司"]
            except:
                companies = ["預設公司"]
            return jsonify({
                "pay_mode": row[0],
                "default_rate": row[1],
                "currency": row[2],
                "companies": companies
            })
        else:
            return jsonify({
                "pay_mode": "day",
                "default_rate": 700,
                "currency": "$",
                "companies": ["預設公司"]
            })

    if request.method == 'POST':
        data = request.json or {}
        target_user = data.get('target_user', get_current_user())
        if not check_permission(target_user):
            return jsonify({"error": "Forbidden"}), 403

        pay_mode = data.get('pay_mode', 'day')
        default_rate = float(data.get('default_rate', 700))
        currency = data.get('currency', '$')
        companies = data.get('companies', ['預設公司'])

        companies = [c.strip() for c in companies if c and c.strip()]
        if not companies:
            companies = ['預設公司']

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

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
        return jsonify({"success": True})

@app.route('/api/work/<int:year>/<int:month>', methods=['GET'])
def get_work_logs(year, month):
    if not get_current_user():
        return jsonify({"error": "Unauthorized"}), 401

    target_user = request.args.get('target_user', get_current_user())
    if not check_permission(target_user):
        return jsonify({"error": "Forbidden"}), 403

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    prefix = f"{year}-{month:02d}-%"
    c.execute('''
        SELECT id, date, company, pay_mode, status, rate, hours, daily_pay, location 
        FROM work_logs 
        WHERE username=? AND date LIKE ?
        ORDER BY date ASC, id ASC
    ''', (target_user, prefix))
    
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

    return jsonify({
        "logs": logs,
        "summary": {
            "total_hours": round(total_hours, 2),
            "total_salary": round(total_salary, 2),
            "by_company": by_company
        }
    })

@app.route('/api/work/save', methods=['POST'])
def save_work_log():
    if not get_current_user():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    target_user = data.get('target_user', get_current_user())
    if not check_permission(target_user):
        return jsonify({"error": "Forbidden"}), 403

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
        multipliers = {'half': 0.5, 'full': 1.0, 'ot_1.5': 1.5, 'ot_2.0': 2.0, 'ot_3.0': 3.0}
        daily_pay = rate * multipliers.get(status, 1.0)
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
    return jsonify({"success": True})

@app.route('/api/work/delete_item', methods=['POST'])
def delete_work_item():
    if not get_current_user():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    target_user = data.get('target_user', get_current_user())
    if not check_permission(target_user):
        return jsonify({"error": "Forbidden"}), 403

    log_id = data.get('log_id')

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM work_logs WHERE id=? AND username=?", (log_id, target_user))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# --- 會計審核 API ---

@app.route('/api/accounting/work/<int:year>/<int:month>', methods=['GET'])
def get_accounting_work_logs(year, month):
    role = get_user_role()
    if role not in ['accounting', 'admin']:
        return jsonify({"error": "Forbidden: Accounting Mode Only"}), 403

    acct_company = get_user_company()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT username, display_name FROM users")
    user_names = {row[0]: (row[1] if row[1] else row[0]) for row in c.fetchall()}

    prefix = f"{year}-{month:02d}-%"

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
        un = r[2]
        logs.append({
            "id": r[0],
            "original_log_id": r[1],
            "username": un,
            "display_name": user_names.get(un, un),
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
        "company": acct_company if role != 'admin' else "管理者全公司檢視",
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

    daily_pay = 0.0
    if pay_mode == 'day':
        multipliers = {'half': 0.5, 'full': 1.0, 'ot_1.5': 1.5, 'ot_2.0': 2.0, 'ot_3.0': 3.0}
        daily_pay = rate * multipliers.get(status, 1.0)
    else:
        daily_pay = rate * hours

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    if role != 'admin':
        c.execute('SELECT company FROM accounting_work_logs WHERE id=?', (log_id,))
        row = c.fetchone()
        if not row or row[0] != acct_company:
            conn.close()
            return jsonify({"error": "Forbidden"}), 403

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
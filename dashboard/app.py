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
    
    # 自動補齊欄位
    c.execute("PRAGMA table_info(users)")
    user_cols = [col[1] for col in c.fetchall()]
    if 'role' not in user_cols:
        c.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'employee'")
    if 'company' not in user_cols:
        c.execute("ALTER TABLE users ADD COLUMN company TEXT DEFAULT ''")
    if 'display_name' not in user_cols:
        c.execute("ALTER TABLE users ADD COLUMN display_name TEXT DEFAULT ''")

    # 預設 admin 帳號
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

@app.route('/api/login', methods=['POST'])
def login():
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

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True})

@app.route('/api/me', methods=['GET'])
def me():
    username = get_current_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT display_name, role, company, is_admin FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()

    display_name = row[0] if row and row[0] else username
    session['display_name'] = display_name

    return jsonify({
        "username": username, 
        "display_name": display_name,
        "is_admin": is_admin(),
        "role": get_user_role(),
        "company": get_user_company()
    })

# 功能 1：一般使用者修改自己的 Display Name
@app.route('/api/user/profile', methods=['POST'])
def update_profile():
    username = get_current_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    new_display_name = data.get('display_name', '').strip()

    if not new_display_name:
        return jsonify({"error": "顯示名稱不能為空"}), 400

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET display_name=? WHERE username=?", (new_display_name, username))
    conn.commit()
    conn.close()

    session['display_name'] = new_display_name
    return jsonify({"success": True, "display_name": new_display_name})

# 功能 2：管理者取得所有使用者列表
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

# 功能 3：管理者修改任意使用者的 Display Name / Role / Company
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

    if target_user == get_current_user():
        session['display_name'] = new_display_name or target_user

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
import os
import sqlite3
from flask import Flask, request, jsonify, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
import json

app = Flask(__name__, static_folder='static')
app.secret_key = 'super_secret_key_change_me_in_production'
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
    
    # 檢查並補齊 missing 的欄位
    c.execute("PRAGMA table_info(work_logs)")
    columns = [col[1] for col in c.fetchall()]
    if 'company' not in columns:
        c.execute("ALTER TABLE work_logs ADD COLUMN company TEXT DEFAULT '預設公司'")

    c.execute("PRAGMA table_info(user_settings)")
    settings_cols = [col[1] for col in c.fetchall()]
    if 'companies' not in settings_cols:
        c.execute("ALTER TABLE user_settings ADD COLUMN companies TEXT DEFAULT '[\"預設公司\"]'")

    # 建立預設管理者帳號
    c.execute("SELECT * FROM users WHERE username='eason'")
    if not c.fetchone():
        hashed_pwd = generate_password_hash("eason")
        c.execute("INSERT INTO users (username, password, is_admin) VALUES (?, ?, 1)", ("eason", hashed_pwd))
        c.execute("INSERT OR IGNORE INTO user_settings (username, pay_mode, default_rate, currency, companies) VALUES (?, 'day', 700, '$', '[\"預設公司\"]')", ("eason",))

    conn.commit()
    conn.close()

init_db()

def get_current_user():
    return session.get('username')

def is_admin():
    return session.get('is_admin', 0) == 1

def check_permission(target_user):
    user = get_current_user()
    if not user:
        return False
    if user == target_user or is_admin():
        return True
    return False

@app.route('/')
def serve_index():
    if not get_current_user():
        return send_from_directory('static', 'login.html')
    return send_from_directory('static', 'index.html')

@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT password, is_admin FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()

    if row and check_password_hash(row[0], password):
        session['username'] = username
        session['is_admin'] = row[1]
        return jsonify({"success": True, "username": username, "is_admin": row[1]})
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
    return jsonify({"username": username, "is_admin": is_admin()})

@app.route('/api/admin/users', methods=['GET'])
def list_users():
    if not is_admin():
        return jsonify({"error": "Forbidden"}), 403
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT username FROM users")
    users = [row[0] for row in c.fetchall()]
    conn.close()
    return jsonify(users)

@app.route('/api/admin/users/delete', methods=['POST'])
def delete_user():
    if not is_admin():
        return jsonify({"error": "Forbidden"}), 403
    data = request.json or {}
    target_user = data.get('target_user')
    if target_user == get_current_user():
        return jsonify({"error": "Cannot delete current logged in admin"}), 400

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE username=?", (target_user,))
    c.execute("DELETE FROM user_settings WHERE username=?", (target_user,))
    c.execute("DELETE FROM work_logs WHERE username=?", (target_user,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify({"error": "請輸入帳號和密碼"}), 400

    hashed_pwd = generate_password_hash(password)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, password, is_admin) VALUES (?, ?, 0)", (username, hashed_pwd))
        c.execute("INSERT INTO user_settings (username, pay_mode, default_rate, currency, companies) VALUES (?, 'day', 700, '$', '[\"預設公司\"]')", (username,))
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

        # 過濾空白名稱
        companies = [c.strip() for c in companies if c and c.strip()]
        if not companies:
            companies = ['預設公司']

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # 查出舊的設定
        c.execute("SELECT companies FROM user_settings WHERE username=?", (target_user,))
        old_row = c.fetchone()
        old_companies = []
        if old_row and old_row[0]:
            try:
                old_companies = json.loads(old_row[0])
            except:
                pass

        # 核心邏輯：如果舊公司清單中包含 "預設公司"，且新設定的公司清單已經不含 "預設公司"
        if "預設公司" in old_companies and "預設公司" not in companies:
            first_new_company = companies[0]
            # 將歷史所有標記為 "預設公司" 的舊紀錄全數更新為新公司名稱
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
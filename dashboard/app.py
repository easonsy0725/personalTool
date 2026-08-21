import os
import sqlite3
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template, session, redirect, url_for, send_file
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder='static', template_folder='static')
app.secret_key = 'your_secret_key_here'
DB_NAME = 'database.db'

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        # 使用者表：role 可為 'admin', 'employee', 'accounting'
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'employee',
                company TEXT -- 若為 accounting，綁定其可管理的公司名稱
            )
        ''')
        # 工作紀錄表：新增 source 欄位 ('employee' 或 'accounting')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS work_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                date TEXT NOT NULL,
                company TEXT NOT NULL,
                pay_mode TEXT NOT NULL,
                status TEXT NOT NULL,
                rate REAL NOT NULL,
                hours REAL NOT NULL,
                daily_pay REAL NOT NULL,
                location TEXT,
                source TEXT NOT NULL DEFAULT 'employee'
            )
        ''')
        # 設定檔表
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                username TEXT PRIMARY KEY,
                pay_mode TEXT DEFAULT 'day',
                default_rate REAL DEFAULT 700,
                currency TEXT DEFAULT '$',
                companies TEXT DEFAULT '預設公司'
            )
        ''')
        conn.commit()

init_db()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated_function

@app.route('/api/me')
@login_required
def get_me():
    with get_db() as conn:
        user = conn.execute("SELECT username, role, company FROM users WHERE username = ?", (session['username'],)).fetchone()
        return jsonify({
            'username': user['username'],
            'role': user['role'],
            'company': user['company']
        })

@app.route('/api/users')
@login_required
def get_users():
    # 管理員與會計皆可取得員工選單
    if session.get('role') not in ['admin', 'accounting']:
        return jsonify([])
    with get_db() as conn:
        users = conn.execute("SELECT username FROM users WHERE role = 'employee'").fetchall()
        return jsonify([u['username'] for u in users])

@app.route('/api/work/<int:year>/<int:month>')
@login_required
def get_work_logs(year, month):
    current_user = session['username']
    user_role = session.get('role', 'employee')
    user_company = session.get('company')
    
    target_user = request.args.get('target_user', current_user)
    
    # 權限判定：一般員工只能看自己
    if user_role == 'employee' and target_user != current_user:
        target_user = current_user

    month_str = f"{year}-{month:02d}"
    
    with get_db() as conn:
        # 會計視角處理
        if user_role == 'accounting':
            # 只抓取該會計所屬公司的資料
            query = """
                SELECT * FROM work_logs 
                WHERE username = ? AND date LIKE ? AND company = ?
            """
            rows = conn.execute(query, (target_user, f"{month_str}%", user_company)).fetchall()
        else:
            # 一般員工/管理員：抓取該使用者所有的申報資料 (source='employee')
            query = """
                SELECT * FROM work_logs 
                WHERE username = ? AND date LIKE ? AND source = 'employee'
            """
            rows = conn.execute(query, (target_user, f"{month_str}%")).fetchall()

        logs = {}
        total_hours = 0
        total_salary = 0
        by_company = {}

        for row in rows:
            item = dict(row)
            d = item['date']
            if d not in logs: logs[d] = []
            logs[d].append(item)

            comp = item['company']
            hrs = item['hours']
            pay = item['daily_pay']

            total_hours += hrs
            total_salary += pay

            if comp not in by_company:
                by_company[comp] = {'hours': 0, 'salary': 0}
            by_company[comp]['hours'] += hrs
            by_company[comp]['salary'] += pay

        return jsonify({
            'logs': logs,
            'summary': {
                'total_hours': total_hours,
                'total_salary': total_salary,
                'by_company': by_company
            }
        })

@app.route('/api/work/save', methods=['POST'])
@login_required
def save_work():
    data = request.json
    current_user = session['username']
    user_role = session.get('role', 'employee')
    user_company = session.get('company')

    target_user = data.get('target_user', current_user)
    if user_role == 'employee':
        target_user = current_user
        company = data.get('company', '預設公司')
        source = 'employee'
    elif user_role == 'accounting':
        # 會計強制寫入自己綁定的公司與 accounting 來源
        company = user_company
        source = 'accounting'
    else:
        company = data.get('company', '預設公司')
        source = 'employee'

    pay_mode = data.get('pay_mode', 'day')
    status = data.get('status', 'full')
    rate = float(data.get('rate', 0))
    hours = float(data.get('hours', 0))
    location = data.get('location', '')
    date = data.get('date')
    log_id = data.get('id')

    # 計算薪資
    if pay_mode == 'day':
        multipliers = {'half': 0.5, 'full': 1.0, 'ot_1.5': 1.5, 'ot_2.0': 2.0, 'ot_3.0': 3.0}
        daily_pay = rate * multipliers.get(status, 1.0)
        if status == 'half': hours = 4.0
        elif status == 'full': hours = 8.0
    else:
        daily_pay = rate * hours

    with get_db() as conn:
        if log_id:
            conn.execute('''
                UPDATE work_logs 
                SET company=?, pay_mode=?, status=?, rate=?, hours=?, daily_pay=?, location=?
                WHERE id=?
            ''', (company, pay_mode, status, rate, hours, daily_pay, location, log_id))
        else:
            conn.execute('''
                INSERT INTO work_logs (username, date, company, pay_mode, status, rate, hours, daily_pay, location, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (target_user, date, company, pay_mode, status, rate, hours, daily_pay, location, source))
        conn.commit()

    return jsonify({'status': 'success'})

@app.route('/api/work/delete_item', methods=['POST'])
@login_required
def delete_work():
    data = request.json
    log_id = data.get('log_id')
    with get_db() as conn:
        conn.execute("DELETE FROM work_logs WHERE id = ?", (log_id,))
        conn.commit()
    return jsonify({'status': 'success'})

@app.route('/api/settings', methods=['GET', 'POST'])
@login_required
def handle_settings():
    username = session['username']
    with get_db() as conn:
        if request.method == 'POST':
            data = request.json
            companies_str = ",".join(data.get('companies', ['預設公司']))
            conn.execute('''
                INSERT INTO user_settings (username, pay_mode, default_rate, currency, companies)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    pay_mode=excluded.pay_mode,
                    default_rate=excluded.default_rate,
                    currency=excluded.currency,
                    companies=excluded.companies
            ''', (username, data.get('pay_mode'), data.get('default_rate'), data.get('currency'), companies_str))
            conn.commit()
            return jsonify({'status': 'success'})
        else:
            row = conn.execute("SELECT * FROM user_settings WHERE username = ?", (username,)).fetchone()
            if row:
                res = dict(row)
                res['companies'] = res['companies'].split(',') if res['companies'] else ['預設公司']
                return jsonify(res)
            return jsonify({'pay_mode': 'day', 'default_rate': 700, 'currency': '$', 'companies': ['預設公司']})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
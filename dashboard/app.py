import os
import sqlite3
from flask import Flask, request, jsonify, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder='static', template_folder='static')

app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'my_default_super_secret_key_12345')

# 設定 SQLite 資料庫路徑為 data/dashboard.db
DB_DIR = 'data'
DB_NAME = os.path.join(DB_DIR, 'dashboard.db')

def get_db():
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'employee',
                company TEXT
            )
        ''')
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
                source TEXT DEFAULT 'employee'
            )
        ''')
        conn.commit()

init_db()

@app.route('/')
def index():
    if 'username' not in session:
        return redirect('/login.html')
    return app.send_static_file('index.html')

@app.route('/login.html')
def login_page():
    return app.send_static_file('login.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    username = data.get('username')
    password = data.get('password')
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user['password'], password):
            session['username'] = user['username']
            session['role'] = user['role']
            return jsonify({'status': 'success'})
        return jsonify({'error': '帳號或密碼錯誤'}), 400

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'status': 'success'})

@app.route('/api/me')
def get_me():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({
        'username': session['username'],
        'role': session.get('role', 'admin')
    })

@app.route('/api/users')
def get_users():
    with get_db() as conn:
        users = conn.execute("SELECT DISTINCT username FROM users UNION SELECT username FROM work_logs").fetchall()
        return jsonify([u['username'] for u in users])

@app.route('/api/work/<int:year>/<int:month>')
def get_work_logs(year, month):
    target_user = request.args.get('target_user', session.get('username'))
    month_str = f"{year}-{month:02d}"
    
    with get_db() as conn:
        query = "SELECT * FROM work_logs WHERE username = ? AND date LIKE ?"
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
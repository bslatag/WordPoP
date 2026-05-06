from flask import Flask, render_template, request, jsonify, session, redirect, url_for, g
import sqlite3
import random
import os
import re

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'wordpop-secret-key')

DATABASE = 'wordpop.db'

def get_db():
    if '_database' not in g:
        g._database = sqlite3.connect(DATABASE)
        g._database.row_factory = sqlite3.Row
    return g._database

@app.teardown_appcontext
def close_connection(exception):
    db = g.pop('_database', None)
    if db is not None:
        db.close()

_db_initialized = False

@app.before_request
def init_db_on_first_request():
    global _db_initialized
    if not _db_initialized:
        db = get_db()
        # 用户及学习记录表
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS learned_words (
                user_id INTEGER,
                word TEXT NOT NULL,
                PRIMARY KEY (user_id, word),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS starred_words (
                user_id INTEGER,
                word TEXT NOT NULL,
                PRIMARY KEY (user_id, word),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS selfstudy_words (
                user_id INTEGER,
                word TEXT NOT NULL,
                PRIMARY KEY (user_id, word),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            -- 单词词库表
            CREATE TABLE IF NOT EXISTS dictionary (
                word TEXT PRIMARY KEY,
                meaning TEXT,
                phonetic TEXT
            );
        """)
        db.commit()

        # 检查并导入 Excel 数据到 dictionary 表
        cursor = db.execute("SELECT COUNT(*) FROM dictionary")
        if cursor.fetchone()[0] == 0:
            try:
                import pandas as pd
                df = pd.read_excel("word.xls")
            except Exception as e:
                print("⚠️  Excel 读取失败，将无法提供释义校验：", e)
                df = None
            if df is not None:
                for _, row in df.iterrows():
                    word = str(row.get('单词', '')).strip().lower()
                    meaning = str(row.get('释义', ''))
                    phonetic = str(row.get('音标', ''))
                    if word:
                        db.execute("INSERT OR IGNORE INTO dictionary (word, meaning, phonetic) VALUES (?, ?, ?)",
                                   (word, meaning, phonetic))
                db.commit()
                print(f"✅ 已从 Excel 导入 {len(df)} 个单词到数据库")

            # 释放 pandas 占用的内存（可选）
            import sys
            if 'pandas' in sys.modules:
                del sys.modules['pandas']

        _db_initialized = True

# ------------------ 用户系统 ------------------
def get_current_user():
    user_id = session.get('user_id')
    if user_id:
        return user_id
    db = get_db()
    cursor = db.execute("INSERT INTO users (username) VALUES (NULL)")
    db.commit()
    session['user_id'] = cursor.lastrowid
    return session['user_id']

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        if not username:
            return render_template('login.html', error='用户名不能为空')
        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            guest_id = session.get('user_id')
            if guest_id and guest_id != existing['id']:
                for table in ['learned_words', 'starred_words', 'selfstudy_words']:
                    rows = db.execute(f"SELECT word FROM {table} WHERE user_id = ?", (guest_id,)).fetchall()
                    for row in rows:
                        db.execute(f"INSERT OR IGNORE INTO {table} (user_id, word) VALUES (?, ?)",
                                   (existing['id'], row['word']))
                db.execute("DELETE FROM users WHERE id = ?", (guest_id,))
                db.commit()
            session['user_id'] = existing['id']
        else:
            guest_id = session.get('user_id')
            if guest_id:
                db.execute("UPDATE users SET username = ? WHERE id = ?", (username, guest_id))
                db.commit()
            else:
                cursor = db.execute("INSERT INTO users (username) VALUES (?)", (username,))
                db.commit()
                session['user_id'] = cursor.lastrowid
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# ------------------ 页面路由 ------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/study')
def study():
    return render_template('study.html')

@app.route('/writing')
def writing():
    return render_template('writing.html')

@app.route('/writing/practice')
def writing_practice():
    return render_template('writing_practice.html')

# ------------------ API 路由 ------------------
@app.route('/api/user')
def api_user():
    user_id = get_current_user()
    db = get_db()
    user = db.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    return jsonify({
        'id': user['id'],
        'username': user['username'],
        'is_guest': user['username'] is None
    })

@app.route('/api/study-words')
def api_study_words():
    user_id = get_current_user()
    db = get_db()
    learned = set(row['word'] for row in db.execute(
        "SELECT word FROM learned_words WHERE user_id = ?", (user_id,)).fetchall())
    # 从 dictionary 表随机取 30 个未学单词
    all_words = db.execute("SELECT word, meaning, phonetic FROM dictionary ORDER BY RANDOM() LIMIT 100").fetchall()
    candidates = [{'单词': row['word'], '释义': row['meaning'], '音标': row['phonetic']}
                  for row in all_words if row['word'] not in learned]
    random.shuffle(candidates)
    selected = candidates[:30]
    return jsonify(selected)

@app.route('/api/mark-learned', methods=['POST'])
def api_mark_learned():
    user_id = get_current_user()
    words = request.json.get('words', [])
    db = get_db()
    for word in words:
        db.execute("INSERT OR IGNORE INTO learned_words (user_id, word) VALUES (?, ?)", (user_id, word))
    db.commit()
    return jsonify({'status': 'ok'})

@app.route('/api/word-list/<list_type>')
def api_word_list(list_type):
    user_id = get_current_user()
    db = get_db()
    table_map = {
        'learned': 'learned_words',
        'starred': 'starred_words',
        'selfstudy': 'selfstudy_words'
    }
    table = table_map.get(list_type)
    if not table:
        return jsonify([])
    rows = db.execute(f"SELECT word FROM {table} WHERE user_id = ?", (user_id,)).fetchall()
    return jsonify([row['word'] for row in rows])

@app.route('/api/add-to-list', methods=['POST'])
def api_add_to_list():
    user_id = get_current_user()
    word = request.json.get('word', '').strip().lower()
    if not re.match(r'^[a-zA-Z]{2,20}$', word):
        return jsonify({'error': '无效单词格式'}), 400
    # 检查是否在词库中
    db = get_db()
    in_dict = db.execute("SELECT 1 FROM dictionary WHERE word = ?", (word,)).fetchone() is not None
    db.execute("INSERT OR IGNORE INTO selfstudy_words (user_id, word) VALUES (?, ?)", (user_id, word))
    db.commit()
    return jsonify({'status': 'ok', 'in_dict': in_dict})

@app.route('/api/remove-from-list', methods=['POST'])
def api_remove_from_list():
    user_id = get_current_user()
    word = request.json.get('word')
    db = get_db()
    db.execute("DELETE FROM selfstudy_words WHERE user_id = ? AND word = ?", (user_id, word))
    db.commit()
    return jsonify({'status': 'ok'})

@app.route('/api/toggle-star', methods=['POST'])
def api_toggle_star():
    user_id = get_current_user()
    word = request.json.get('word')
    db = get_db()
    row = db.execute("SELECT 1 FROM starred_words WHERE user_id = ? AND word = ?", (user_id, word)).fetchone()
    if row:
        db.execute("DELETE FROM starred_words WHERE user_id = ? AND word = ?", (user_id, word))
        action = 'removed'
    else:
        db.execute("INSERT OR IGNORE INTO starred_words (user_id, word) VALUES (?, ?)", (user_id, word))
        action = 'added'
    db.commit()
    return jsonify({'status': 'ok', 'action': action})

@app.route('/api/check-word')
def api_check_word():
    word = request.args.get('word', '').strip().lower()
    db = get_db()
    row = db.execute("SELECT meaning FROM dictionary WHERE word = ?", (word,)).fetchone()
    if row:
        return jsonify({'valid': True, 'meaning': row['meaning']})
    return jsonify({'valid': False, 'meaning': ''})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

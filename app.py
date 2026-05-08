from flask import Flask, render_template, request, jsonify, session, redirect, url_for, g
import sqlite3, random, os, re

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
            # 已有用户：合并游客数据
            guest_id = session.get('user_id')
            if guest_id and guest_id != existing['id']:
                for table in ['learned_words', 'starred_words', 'selfstudy_words']:
                    rows = db.execute(f"SELECT word FROM {table} WHERE user_id = ?", (guest_id,)).fetchall()
                    for row in rows:
                        db.execute(f"INSERT OR IGNORE INTO {table} (user_id, word) VALUES (?,?)",
                                   (existing['id'], row['word']))
                db.execute("DELETE FROM users WHERE id = ?", (guest_id,))
                db.commit()
            session['user_id'] = existing['id']   # ← 关键：设置 Session
        else:
            # 新用户注册
            cursor = db.execute("INSERT INTO users (username) VALUES (?)", (username,))
            db.commit()
            session['user_id'] = cursor.lastrowid
        return redirect(url_for('index'))
    return render_template('login.html')
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))
    
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

@app.route('/api/user')
def api_user():
    user_id = get_current_user()
    db = get_db()
    user = db.execute("SELECT id, username, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    
    pending = db.execute("SELECT COUNT(*) as count FROM learned_words WHERE user_id = ?", (user_id,)).fetchone()
    
    days = 1
    if user and user['created_at']:
        try:
            from datetime import datetime
            created = datetime.strptime(user['created_at'], '%Y-%m-%d %H:%M:%S')
            days = (datetime.now() - created).days + 1
        except:
            days = 1
    
    return jsonify({
        'id': user['id'] if user else 0,
        'username': user['username'] if user else None,
        'is_guest': user['username'] is None if user else True,
        'study_days': days,
        'pending_words': pending['count'] if pending else 0
    })

@app.route('/api/study-words')
def api_study_words():
    user_id = get_current_user()
    db = get_db()
    learned = set(row['word'] for row in db.execute("SELECT word FROM learned_words WHERE user_id = ?", (user_id,)).fetchall())
    all_words = db.execute("SELECT word, meaning, phonetic FROM dictionary ORDER BY RANDOM() LIMIT 200").fetchall()
    candidates = [{'单词': row['word'], '释义': row['meaning'], '音标': row['phonetic']}
                  for row in all_words if row['word'] not in learned]
    if len(candidates) < 30:
        all_available = db.execute("SELECT word, meaning, phonetic FROM dictionary ORDER BY RANDOM() LIMIT 50").fetchall()
        more = [{'单词': row['word'], '释义': row['meaning'], '音标': row['phonetic']}
                for row in all_available if row['word'] not in [c['单词'] for c in candidates]]
        candidates.extend(more[:30-len(candidates)])
    random.shuffle(candidates)
    selected = candidates[:30]
    return jsonify(selected)

@app.route('/api/mark-learned', methods=['POST'])
def api_mark_learned():
    user_id = get_current_user()
    words = request.json.get('words', [])
    db = get_db()
    for word in words:
        db.execute("INSERT OR IGNORE INTO learned_words (user_id, word) VALUES (?,?)", (user_id, word))
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
    words = [row['word'] for row in rows]

    meaning_map = {}
    if words:
        placeholders = ','.join(['?' for _ in words])
        query = f"SELECT word, meaning FROM dictionary WHERE word IN ({placeholders})"
        meanings = db.execute(query, words).fetchall()
        for m in meanings:
            meaning_map[m['word']] = m['meaning'] or '暂无释义'
    result = [{'word': w, 'meaning': meaning_map.get(w, '暂无释义')} for w in words]
    return jsonify(result)

@app.route('/api/add-to-list', methods=['POST'])
def api_add_to_list():
    user_id = get_current_user()
    word = request.json.get('word', '').strip().lower()
    if not re.match(r'^[a-zA-Z]{2,20}$', word):
        return jsonify({'error': '无效单词格式'}), 400

    db = get_db()
    # 检查词典中是否存在
    exists = db.execute("SELECT 1 FROM dictionary WHERE word = ?", (word,)).fetchone()
    if not exists:
        # 联网查询释义
        meaning = ''
        try:
            resp = requests.get(
                f'https://dict.youdao.com/suggest?q={word}&le=eng',
                timeout=3
            )
            data = resp.json()
            if data.get('data') and data['data'].get('entries'):
                entries = data['data']['entries']
                if entries and entries[0].get('explain'):
                    meaning = entries[0]['explain']
        except Exception:
            pass
        if not meaning:
            meaning = '暂无释义'
        db.execute(
            "INSERT OR IGNORE INTO dictionary (word, meaning) VALUES (?, ?)",
            (word, meaning)
        )
        db.commit()

    db.execute(
        "INSERT OR IGNORE INTO selfstudy_words (user_id, word) VALUES (?, ?)",
        (user_id, word)
    )
    db.commit()
    return jsonify({'status': 'ok'})

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
        db.execute("INSERT OR IGNORE INTO starred_words (user_id, word) VALUES (?,?)", (user_id, word))
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

@app.route('/api/dictionary')
def api_dictionary():
    db = get_db()
    rows = db.execute("SELECT word, meaning FROM dictionary").fetchall()
    return jsonify({row['word']: row['meaning'] for row in rows})

@app.route('/api/skip-word', methods=['POST'])
def api_skip_word():
    user_id = get_current_user()
    word = request.json.get('word', '').strip().lower()
    db = get_db()
    db.execute("INSERT OR IGNORE INTO skipped_words (user_id, word) VALUES (?,?)", (user_id, word))
    db.commit()
    learned = set(row['word'] for row in db.execute("SELECT word FROM learned_words WHERE user_id = ?", (user_id,)).fetchall())
    skipped = set(row['word'] for row in db.execute("SELECT word FROM skipped_words WHERE user_id = ?", (user_id,)).fetchall())
    exclude = learned | skipped
    all_words = db.execute("SELECT word, meaning, phonetic FROM dictionary ORDER BY RANDOM()").fetchall()
    new_word = None
    for row in all_words:
        if row['word'] not in exclude:
            new_word = {'单词': row['word'], '释义': row['meaning'], '音标': row['phonetic']}
            break
    return jsonify({'new_word': new_word})

if __name__ == '__main__':
    with app.app_context():
        db = get_db()
        db.execute("""
            CREATE TABLE IF NOT EXISTS skipped_words (
                user_id INTEGER,
                word TEXT NOT NULL,
                PRIMARY KEY (user_id, word),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)
        db.commit()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

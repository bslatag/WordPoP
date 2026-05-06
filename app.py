from flask import Flask, render_template, request, jsonify, session, redirect, url_for, g
import sqlite3, random, os, re, io, pytesseract
from PIL import Image, ImageFilter

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

# ── 用户系统 ──
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
                        db.execute(f"INSERT OR IGNORE INTO {table} (user_id, word) VALUES (?,?)",
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

# ── 页面 ──
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

# ── API ──
@app.route('/api/user')
def api_user():
    user_id = get_current_user()
    db = get_db()
    user = db.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    return jsonify({'id': user['id'], 'username': user['username'], 'is_guest': user['username'] is None})

@app.route('/api/study-words')
def api_study_words():
    user_id = get_current_user()
    db = get_db()
    learned = set(row['word'] for row in db.execute("SELECT word FROM learned_words WHERE user_id = ?", (user_id,)).fetchall())
    all_words = db.execute("SELECT word, meaning, phonetic FROM dictionary ORDER BY RANDOM() LIMIT 200").fetchall()
    candidates = [{'单词': row['word'], '释义': row['meaning'], '音标': row['phonetic']}
                  for row in all_words if row['word'] not in learned]
    if len(candidates) < 30:
        # 如果不足30个，用已学单词补足（避免空数组）
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
    table_map = {'learned': 'learned_words', 'starred': 'starred_words', 'selfstudy': 'selfstudy_words'}
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
    db = get_db()
    in_dict = db.execute("SELECT 1 FROM dictionary WHERE word = ?", (word,)).fetchone() is not None
    db.execute("INSERT OR IGNORE INTO selfstudy_words (user_id, word) VALUES (?,?)", (user_id, word))
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

# ── OCR ──
@app.route('/api/ocr', methods=['POST'])
def api_ocr():
    if 'image' not in request.files:
        return jsonify({'words': [], 'error': 'no image'}), 400
    file = request.files['image']
    img_bytes = file.read()
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert('L')
    except Exception:
        return jsonify({'words': [], 'error': '图片无法解析'}), 400
    # 压缩
    w, h = img.size
    max_size = 1200
    if w > max_size or h > max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)
    # 预处理
    img = img.filter(ImageFilter.SHARPEN)
    img = img.point(lambda x: 0 if x < 140 else 255)
    # OCR
    try:
        text = pytesseract.image_to_string(img, lang='eng', config='--psm 6')
    except Exception as e:
        return jsonify({'words': [], 'error': f'识别出错: {str(e)}'}), 500
    candidates = re.findall(r'[a-zA-Z]{2,20}', text.lower())
    db = get_db()
    valid_words = set(row['word'] for row in db.execute("SELECT word FROM dictionary").fetchall())
    filtered = [w for w in candidates if w in valid_words]
    unique = list(dict.fromkeys(filtered))
    return jsonify({'words': unique, 'raw_count': len(candidates), 'filtered_count': len(unique)})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

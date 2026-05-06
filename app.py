from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import pandas as pd
import random
import os
import sqlite3
import re
import numpy as np
import cv2
import pytesseract
from PIL import Image
from io import BytesIO

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'a-very-secret-key-for-session')

DATABASE = 'wordpop.db'

# ------------------ 数据库工具 ------------------
def get_db():
    db = getattr(app, '_database', None)
    if db is None:
        db = app._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(app, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        with app.open_resource('schema.sql', mode='r') as f:
            db.cursor().executescript(f.read())
        db.commit()

# 获取当前用户id，游客自动创建临时用户
def get_current_user():
    user_id = session.get('user_id')
    if user_id:
        return user_id
    # 创建游客
    db = get_db()
    cursor = db.execute("INSERT INTO users (username) VALUES (NULL)")
    db.commit()
    session['user_id'] = cursor.lastrowid
    return session['user_id']

# 正式登录绑定用户名
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        if not username:
            return render_template('login.html', error='用户名不能为空')
        db = get_db()
        # 查找现有用户
        existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            # 合并数据：将游客数据转移到正式用户
            guest_id = session.get('user_id')
            if guest_id and guest_id != existing['id']:
                # 合并已学、收藏、自习室
                for table in ['learned_words', 'starred_words', 'selfstudy_words']:
                    rows = db.execute(f"SELECT word FROM {table} WHERE user_id = ?", (guest_id,)).fetchall()
                    for row in rows:
                        db.execute(f"INSERT OR IGNORE INTO {table} (user_id, word) VALUES (?, ?)",
                                   (existing['id'], row['word']))
                # 删除游客记录
                db.execute("DELETE FROM users WHERE id = ?", (guest_id,))
                db.commit()
            session['user_id'] = existing['id']
        else:
            # 升级游客或新建正式用户
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
# 获取当前用户信息
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

# 获取学习单词（随机30个未学）
@app.route('/api/study-words')
def api_study_words():
    user_id = get_current_user()
    db = get_db()
    learned = set(row['word'] for row in db.execute("SELECT word FROM learned_words WHERE user_id = ?", (user_id,)).fetchall())
    # 读取 Excel
    df = pd.read_excel("word.xls")
    all_words = df.to_dict('records')
    candidates = [w for w in all_words if w['单词'] not in learned]
    random.shuffle(candidates)
    selected = candidates[:30]
    return jsonify(selected)

# 标记已学
@app.route('/api/mark-learned', methods=['POST'])
def api_mark_learned():
    user_id = get_current_user()
    words = request.json.get('words', [])
    db = get_db()
    for word in words:
        db.execute("INSERT OR IGNORE INTO learned_words (user_id, word) VALUES (?, ?)", (user_id, word))
    db.commit()
    return jsonify({'status': 'ok'})

# 获取单词列表
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

# 添加单词到自习室
@app.route('/api/add-to-list', methods=['POST'])
def api_add_to_list():
    user_id = get_current_user()
    word = request.json.get('word', '').strip().lower()
    if not re.match(r'^[a-zA-Z]{2,20}$', word):
        return jsonify({'error': '无效单词格式'}), 400
    # 校验是否在 Excel 词库中
    df = pd.read_excel("word.xls")
    valid_words = set(df['单词'].str.lower())
    in_dict = word in valid_words
    db = get_db()
    db.execute("INSERT OR IGNORE INTO selfstudy_words (user_id, word) VALUES (?, ?)", (user_id, word))
    db.commit()
    return jsonify({'status': 'ok', 'in_dict': in_dict})

# 删除单词
@app.route('/api/remove-from-list', methods=['POST'])
def api_remove_from_list():
    user_id = get_current_user()
    word = request.json.get('word')
    db = get_db()
    db.execute("DELETE FROM selfstudy_words WHERE user_id = ? AND word = ?", (user_id, word))
    db.commit()
    return jsonify({'status': 'ok'})

# 收藏/取消收藏
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

# 检查单词
@app.route('/api/check-word')
def api_check_word():
    word = request.args.get('word', '').strip().lower()
    df = pd.read_excel("word.xls")
    word_df = df[df['单词'].str.lower() == word]
    if not word_df.empty:
        meaning = word_df.iloc[0]['释义']
        return jsonify({'valid': True, 'meaning': meaning})
    return jsonify({'valid': False, 'meaning': ''})

# OCR 识别
@app.route('/api/ocr', methods=['POST'])
def api_ocr():
    if 'image' not in request.files:
        return jsonify({'words': [], 'error': '没有图片'}), 400
    file = request.files['image']
    img_bytes = file.read()
    nparr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return jsonify({'words': [], 'error': '图片无法解析'}), 400
    # 预处理
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 11, 2)
    denoised = cv2.fastNlMeansDenoising(thresh, h=10)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    closed = cv2.morphologyEx(denoised, cv2.MORPH_CLOSE, kernel)
    pil_img = Image.fromarray(closed)
    custom_config = r'--psm 6 -c tessedit_char_whitelist=abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
    text = pytesseract.image_to_string(pil_img, lang='eng', config=custom_config)
    candidates = re.findall(r'[a-zA-Z]{2,}', text)
    # 词库过滤
    df = pd.read_excel("word.xls")
    valid_words = set(df['单词'].str.lower())
    result = [w.lower() for w in candidates if w.lower() in valid_words]
    seen = set()
    unique = []
    for w in result:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return jsonify({'words': unique})

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

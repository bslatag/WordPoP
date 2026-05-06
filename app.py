from flask import Flask, render_template
import pandas as pd
import random
import os

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/study')
def study():
    df = pd.read_excel("word.xls")
    word_list = df.to_dict('records')
    random.shuffle(word_list)
    return render_template('study.html', words=word_list)

@app.route('/writing')
def writing():
    return render_template('writing.html')

@app.route('/writing/practice')
def writing_practice():
    return render_template('writing_practice.html')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

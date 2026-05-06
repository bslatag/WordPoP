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

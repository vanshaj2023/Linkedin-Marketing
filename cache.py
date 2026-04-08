import sqlite3

DB_FILE = "linkedin_agent.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS processed_posts (
            post_id TEXT PRIMARY KEY,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS processed_profiles (
            profile_url TEXT PRIMARY KEY,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def is_post_processed(post_id: str) -> bool:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT 1 FROM processed_posts WHERE post_id = ?', (post_id,))
    result = c.fetchone()
    conn.close()
    return result is not None

def mark_post_processed(post_id: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO processed_posts (post_id) VALUES (?)', (post_id,))
    conn.commit()
    conn.close()

def is_profile_processed(profile_url: str) -> bool:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT 1 FROM processed_profiles WHERE profile_url = ?', (profile_url,))
    result = c.fetchone()
    conn.close()
    return result is not None

def mark_profile_processed(profile_url: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO processed_profiles (profile_url) VALUES (?)', (profile_url,))
    conn.commit()
    conn.close()

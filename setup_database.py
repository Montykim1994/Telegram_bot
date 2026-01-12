import sqlite3
import datetime

db = sqlite3.connect("game.db")
c = db.cursor()

# USERS TABLE
c.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    points INTEGER DEFAULT 0,
    last_guess INTEGER,
    updated_at TEXT
)
""")

# ROUNDS TABLE
c.execute("""
CREATE TABLE IF NOT EXISTS rounds (
    round_id INTEGER PRIMARY KEY AUTOINCREMENT,
    result INTEGER,
    status TEXT,
    ends_at TEXT,
    created_at TEXT
)
""")

# create the first round if none exists
c.execute("SELECT COUNT(*) FROM rounds")
if c.fetchone()[0] == 0:
    ends_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=10)
    c.execute("""
        INSERT INTO rounds (result, status, ends_at, created_at)
        VALUES (?, ?, ?, ?)
    """, (None, "open", ends_at, datetime.datetime.utcnow()))

db.commit()
db.close()

print("Database setup complete!")

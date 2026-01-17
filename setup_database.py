import psycopg2

def get_db():
    return psycopg2.connect(
        host="HOST",
        database="DB",
        user="USER",
        password="PASSWORD"
    )

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        wallet INT DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS add_requests (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        amount INT,
        screenshot_id TEXT,
        created DATE DEFAULT CURRENT_DATE
    )
    """)

    conn.commit()
    conn.close()

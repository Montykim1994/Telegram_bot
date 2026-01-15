import os
import random
import asyncio
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, JobQueue
)

POSTGRES_URL = os.getenv("POSTGRES_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# --------------------------
# DATABASE FUNCTIONS
# --------------------------

def get_db():
    return psycopg2.connect(POSTGRES_URL, sslmode="require")


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        points INT DEFAULT 0
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rounds (
        round_id SERIAL PRIMARY KEY,
        result INT,
        ends_at TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS guesses (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        round_id INT,
        guess INT,
        FOREIGN KEY (round_id) REFERENCES rounds (round_id)
    );
    """)

    conn.commit()
    conn.close()


# --------------------------
# GAME COMMANDS
# --------------------------

POINTS_PER_WIN = 10


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT DO NOTHING;", (user_id,))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        "🎮 Welcome to the Number Guess Game!\n\n"
        "Guess a number between 0–9.\n"
        "Win ⭐ points if you guess correctly!"
    )


async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    row = []
    for i in range(10):
        row.append(InlineKeyboardButton(str(i), callback_data=f"guess_{i}"))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    await update.message.reply_text("Choose a number:", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    guess = int(query.data.split("_")[1])
    user_id = query.from_user.id

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("SELECT * FROM rounds WHERE ends_at > NOW() ORDER BY round_id DESC LIMIT 1;")
    round_data = cur.fetchone()

    if not round_data:
        new_end = datetime.utcnow() + timedelta(minutes=3)
        cur.execute("INSERT INTO rounds (ends_at) VALUES (%s) RETURNING round_id;", (new_end,))
        round_id = cur.fetchone()["round_id"]
        conn.commit()
    else:
        round_id = round_data["round_id"]

    cur.execute(
        "INSERT INTO guesses (user_id, round_id, guess) VALUES (%s, %s, %s);",
        (user_id, round_id, guess)
    )
    conn.commit()
    conn.close()

    await query.edit_message_text(f"👍 Guess `{guess}` saved!")


# --------------------------
# ROUND CHECKER
# --------------------------

async def check_round(context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("SELECT * FROM rounds WHERE result IS NULL AND ends_at <= NOW();")
    expired = cur.fetchall()

    for round_item in expired:
        round_id = round_item["round_id"]
        result = random.randint(0, 9)

        cur.execute("UPDATE rounds SET result=%s WHERE round_id=%s;", (result, round_id))

        cur.execute("SELECT user_id FROM guesses WHERE round_id=%s AND guess=%s;",
                    (round_id, result))

        winners = cur.fetchall()

        for row in winners:
            cur.execute("UPDATE users SET points = points + %s WHERE user_id=%s;",
                        (POINTS_PER_WIN, row["user_id"]))

        conn.commit()

    conn.close()


async def mypoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT points FROM users WHERE user_id=%s;", (user_id,))
    row = cur.fetchone()
    conn.close()

    points = row[0] if row else 0
    await update.message.reply_text(f"⭐ Your Points: {points}")


# --------------------------
# ADMIN PANEL
# --------------------------

ADMIN_ID = 891656290  # your ID


async def admin_only(update: Update):
    return update.effective_user.id == ADMIN_ID


async def addpoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return await update.message.reply_text("❌ You are not admin!")

    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
    except:
        return await update.message.reply_text("Usage: /addpoints user_id amount")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET points = points + %s WHERE user_id=%s;", (amount, user_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✔ Added {amount} points to {user_id}")


async def removepoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return await update.message.reply_text("❌ You are not admin!")

    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
    except:
        return await update.message.reply_text("Usage: /removepoints user_id amount")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET points = GREATEST(points - %s, 0) WHERE user_id=%s;", (amount, user_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✔ Removed {amount} points from {user_id}")


async def setpoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return await update.message.reply_text("❌ You are not admin!")

    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
    except:
        return await update.message.reply_text("Usage: /setpoints user_id amount")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET points = %s WHERE user_id=%s;", (amount, user_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✔ Set {user_id}'s points to {amount}")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return await update.message.reply_text("❌ You are not admin!")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM users;")
    total_users = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM rounds;")
    total_rounds = cur.fetchone()[0]

    conn.close()

    await update.message.reply_text(
        f"📊 **Bot Stats**\n"
        f"👥 Users: {total_users}\n"
        f"🎲 Rounds Played: {total_rounds}"
    )


# --------------------------
# MAIN FUNCTION
# --------------------------

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    job_queue = app.job_queue
    if job_queue is None:
        job_queue = JobQueue()
        job_queue.set_application(app)

    job_queue.run_repeating(check_round, interval=20, first=5)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("play", play))
    app.add_handler(CommandHandler("points", mypoints))
    app.add_handler(CallbackQueryHandler(handle_guess))

    # Admin handlers
    app.add_handler(CommandHandler("addpoints", addpoints))
    app.add_handler(CommandHandler("removepoints", removepoints))
    app.add_handler(CommandHandler("setpoints", setpoints))
    app.add_handler(CommandHandler("stats", stats))

    app.run_polling()


if __name__ == "__main__":
    main()

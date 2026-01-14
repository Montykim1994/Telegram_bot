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
# DB FUNCTIONS
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
            FOREIGN KEY (round_id) REFERENCES rounds(round_id)
        );
    """)

    conn.commit()
    conn.close()


# --------------------------
# GAME LOGIC FUNCTIONS
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
        "Guess a number between 0–9!"
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

    await update.message.reply_text(
        "Choose your number:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    number = int(query.data.split("_")[1])
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

    cur.execute("INSERT INTO guesses (user_id, round_id, guess) VALUES (%s, %s, %s);",
                (user_id, round_id, number))
    conn.commit()

    await query.edit_message_text(f"👍 Your guess `{number}` is saved!")


async def check_round(context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # Process only ONE expired round at a time
    cur.execute("""
        SELECT * FROM rounds 
        WHERE result IS NULL AND ends_at <= NOW()
        ORDER BY round_id ASC
        LIMIT 1;
    """)
    round_item = cur.fetchone()

    if not round_item:
        conn.close()
        return

    round_id = round_item["round_id"]
    result = random.randint(0, 9)

    # update round result
    cur.execute("UPDATE rounds SET result=%s WHERE round_id=%s;", (result, round_id))

    # find winners
    cur.execute("""
        SELECT user_id FROM guesses 
        WHERE round_id=%s AND guess=%s;
    """, (round_id, result))
    winners = cur.fetchall()

    # award points
    for row in winners:
        cur.execute(
            "UPDATE users SET points = points + %s WHERE user_id=%s;",
            (POINTS_PER_WIN, row["user_id"])
        )

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
    await update.message.reply_text(f"⭐ You have {points} points!")


# --------------------------
# MAIN APPLICATION
# --------------------------

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # 🔥 Ensure JobQueue exists
    job_queue = app.job_queue
    if job_queue is None:
        job_queue = JobQueue()
        job_queue.set_application(app)

    # 🔥 Schedule job every 20 seconds
    job_queue.run_repeating(check_round, interval=20, first=5)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("play", play))
    app.add_handler(CommandHandler("points", mypoints))
    app.add_handler(CallbackQueryHandler(handle_guess))
app.add_handler(CommandHandler("addpoints", addpoints))
app.add_handler(CommandHandler("removepoints", removepoints))
app.add_handler(CommandHandler("setpoints", setpoints))
app.add_handler(CommandHandler("stats", stats))
    app.run_polling()

# --------------------------
# ADMIN PANEL
# --------------------------

ADMIN_ID = 891656290

async def admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if user is admin"""
    user_id = update.effective_user.id
    return user_id == ADMIN_ID


async def addpoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return await update.message.reply_text("⛔ Unauthorized")

    if len(context.args) != 2:
        return await update.message.reply_text("Usage: /addpoints <user_id> <amount>")

    user_id = int(context.args[0])
    amount = int(context.args[1])

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET points = points + %s WHERE user_id = %s;", (amount, user_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Added {amount} points to {user_id}")


async def removepoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return await update.message.reply_text("⛔ Unauthorized")

    if len(context.args) != 2:
        return await update.message.reply_text("Usage: /removepoints <user_id> <amount>")

    user_id = int(context.args[0])
    amount = int(context.args[1])

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET points = GREATEST(points - %s, 0) WHERE user_id = %s;", (amount, user_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"❌ Removed {amount} points from {user_id}")


async def setpoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return await update.message.reply_text("⛔ Unauthorized")

    if len(context.args) != 2:
        return await update.message.reply_text("Usage: /setpoints <user_id> <amount>")

    user_id = int(context.args[0])
    amount = int(context.args[1])

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET points = %s WHERE user_id = %s;", (amount, user_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"🔧 Set {user_id}'s points to {amount}")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return await update.message.reply_text("⛔ Unauthorized")

    if not context.args:
        return await update.message.reply_text("Usage: /broadcast <your message>")

    msg = " ".join(context.args)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users;")
    users = cur.fetchall()
    conn.close()

    for row in users:
        try:
            await context.bot.send_message(chat_id=row[0], text=f"📢 Admin Notice:\n\n{msg}")
        except:
            pass

    await update.message.reply_text("📢 Broadcast sent!")


async def forceresult(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return await update.message.reply_text("⛔ Unauthorized")

    if len(context.args) != 1:
        return await update.message.reply_text("Usage: /forceresult <0-9>")

    result = int(context.args[0])
    if result < 0 or result > 9:
        return await update.message.reply_text("❌ Invalid number")

    conn = get_db()
    cur = conn.cursor()

    # update latest unfinished round
    cur.execute("UPDATE rounds SET result = %s WHERE result IS NULL ORDER BY round_id DESC LIMIT 1;", (result,))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"⚠ Forced round result to {result}")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return await update.message.reply_text("⛔ Unauthorized")

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
        f"🎲 Rounds: {total_rounds}\n"
    )
if __name__ == "__main__":
    main()

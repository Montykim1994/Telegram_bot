from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import os
import random
from datetime import datetime, timedelta

# =============================
# Config
# =============================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 891656290

ROUND_DURATION_MINUTES = 10
POINTS_PER_WIN = 10

# =============================
# Game State (memory)
# =============================
users = {}      # user_id -> points
guesses = {}    # user_id -> guess

current_round = {
    "round_id": 1,
    "result": None,
    "ends_at": datetime.utcnow() + timedelta(minutes=ROUND_DURATION_MINUTES)
}

# =============================
# Helpers
# =============================
def generate_result():
    return random.randint(0, 99)

def time_left():
    delta = current_round["ends_at"] - datetime.utcnow()
    seconds = max(0, int(delta.total_seconds()))
    return seconds // 60, seconds % 60

# =============================
# Commands
# =============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users.setdefault(update.effective_user.id, 0)
    m, s = time_left()

    await update.message.reply_text(
        "🎮 Number Guess Game (Points Only)\n\n"
        "⚠️ Disclaimer:\n"
        "Free guessing game. Random results.\n"
        "No money, no betting, no rewards.\n\n"
        f"🕒 Round ID: {current_round['round_id']}\n"
        f"⏳ Time left: {m}m {s}s\n\n"
        "Commands:\n"
        "/play <00-99>\n"
        "/points\n"
        "/result"
    )

async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    users.setdefault(uid, 0)

    if uid in guesses:
        await update.message.reply_text("❗ You already played this round.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /play <00-99>")
        return

    try:
        guess = int(context.args[0])
        if guess < 0 or guess > 99:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a number between 00–99.")
        return

    guesses[uid] = guess
    await update.message.reply_text(f"✅ Guess {guess:02d} submitted.")

async def points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pts = users.get(update.effective_user.id, 0)
    await update.message.reply_text(f"⭐ Your points: {pts}")

async def result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if current_round["result"] is None:
        await update.message.reply_text("ℹ️ Result not declared yet.")
    else:
        await update.message.reply_text(
            f"🏁 Last result: {current_round['result']:02d}\n"
            f"Round ID: {current_round['round_id']}"
        )

# =============================
# ⏱️ Round Timer Job
# =============================
async def round_job(context: ContextTypes.DEFAULT_TYPE):
    global guesses

    result = generate_result()
    current_round["result"] = result

    for uid, g in guesses.items():
        if g == result:
            users[uid] += POINTS_PER_WIN

    # reset for next round
    guesses = {}
    current_round["round_id"] += 1
    current_round["result"] = None
    current_round["ends_at"] = datetime.utcnow() + timedelta(minutes=ROUND_DURATION_MINUTES)

# =============================
# App Setup
# =============================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("play", play))
app.add_handler(CommandHandler("points", points))
app.add_handler(CommandHandler("result", result))

app.job_queue.run_repeating(
    round_job,
    interval=ROUND_DURATION_MINUTES * 60,
    first=ROUND_DURATION_MINUTES * 60
)

app.run_polling()

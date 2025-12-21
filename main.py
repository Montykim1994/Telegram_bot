from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
import os
import random
from datetime import datetime, timedelta

# ==============================
# CONFIG
# ==============================
TOKEN = os.getenv("BOT_TOKEN")  # Set in Railway Variables
ADMIN_ID = 891656290            # Your Telegram ID

ROUND_DURATION_MINUTES = 10
POINTS_PER_WIN = 10

# ==============================
# GAME STATE (IN-MEMORY)
# ==============================
users = {}      # user_id -> points
guesses = {}    # user_id -> guess

current_round = {
    "round_id": 1,
    "ends_at": datetime.utcnow() + timedelta(minutes=ROUND_DURATION_MINUTES),
    "result": None,
}

# ==============================
# HELPERS
# ==============================
def generate_result():
    return random.randint(0, 9)

def time_left():
    delta = current_round["ends_at"] - datetime.utcnow()
    seconds = max(0, int(delta.total_seconds()))
    return seconds // 60, seconds % 60
def check_and_close_round():
    if datetime.utcnow() < current_round["ends_at"]:
        return None  # round still running

    result = generate_result()
    current_round["result"] = result

    winners = 0
    for uid, g in guesses.items():
        if g == result:
            users[uid] += POINTS_PER_WIN
            winners += 1

    guesses.clear()
    current_round["round_id"] += 1
    current_round["ends_at"] = datetime.utcnow() + timedelta(minutes=ROUND_DURATION_MINUTES)
    current_round["result"] = None

    return result, winners
def number_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("0", callback_data="play_0"),
            InlineKeyboardButton("1", callback_data="play_1"),
            InlineKeyboardButton("2", callback_data="play_2"),
            InlineKeyboardButton("3", callback_data="play_3"),
            InlineKeyboardButton("4", callback_data="play_4"),
        ],
        [
            InlineKeyboardButton("5", callback_data="play_5"),
            InlineKeyboardButton("6", callback_data="play_6"),
            InlineKeyboardButton("7", callback_data="play_7"),
            InlineKeyboardButton("8", callback_data="play_8"),
            InlineKeyboardButton("9", callback_data="play_9"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

# ==============================
# COMMANDS
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users.setdefault(update.effective_user.id, 0)
    m, s = time_left()

    await update.message.reply_text(
        "🎮 Number Guess Game (0–9)\n\n"
        "⚠️ Free game • Points only • No money\n\n"
        f"🆔 Round: {current_round['round_id']}\n"
        f"⏳ Time left: {m}m {s}s\n\n"
        "Tap a number below 👇",
        reply_markup=number_keyboard(),
    )

async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Choose a number 👇",
        reply_markup=number_keyboard(),
    )

async def points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pts = users.get(update.effective_user.id, 0)
    await update.message.reply_text(f"⭐ Your points: {pts}")

async def result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if current_round["result"] is None:
        await update.message.reply_text("ℹ️ Result not declared yet.")
    else:
        await update.message.reply_text(
            f"🏁 Last result: {current_round['result']}\n"
            f"Round ID: {current_round['round_id']}"
        )

# ==============================
# BUTTON HANDLER
# ==============================
async def button_play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    users.setdefault(user_id, 0)

    if user_id in guesses:
        await query.edit_message_text("❗ You already played this round.")
        return

    guess = int(query.data.split("_")[1])
    guesses[user_id] = guess

    await query.edit_message_text(
        f"✅ Your guess **{guess}** is locked!\n"
        "Wait for the result ⏳"
    )

# ==============================
# ADMIN: CLOSE ROUND
# ==============================
async def close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Not authorized.")
        return

    result = generate_result()
    current_round["result"] = result

    winners = 0
    for uid, g in guesses.items():
        if g == result:
            users[uid] += POINTS_PER_WIN
            winners += 1

    guesses.clear()
    current_round["round_id"] += 1
    current_round["ends_at"] = datetime.utcnow() + timedelta(minutes=ROUND_DURATION_MINUTES)
    current_round["result"] = None

    await update.message.reply_text(
        f"🎯 Result: {result}\n"
        f"🏆 Winners: {winners}\n"
        "🆕 New round started!"
    )

# ==============================
# APP START
# ==============================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("play", play))
app.add_handler(CommandHandler("points", points))
app.add_handler(CommandHandler("result", result))
app.add_handler(CommandHandler("close", close))  # admin only
app.add_handler(CallbackQueryHandler(button_play, pattern="^play_"))

print("🤖 Bot is running...")
app.run_polling()

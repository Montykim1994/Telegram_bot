from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
import os
import random
from datetime import datetime, timedelta

# =========================
# CONFIG
# =========================
TOKEN = os.getenv("BOT_TOKEN")  # Set in Railway
ADMIN_ID = 891656290

ROUND_DURATION_MINUTES = 10
POINTS_PER_WIN = 10

# =========================
# GAME STATE (memory)
# =========================
users = {}      # user_id -> points
guesses = {}    # user_id -> guessed number

current_round = {
    "round_id": 1,
    "ends_at": datetime.utcnow() + timedelta(minutes=ROUND_DURATION_MINUTES),
    "result": None,
}

# =========================
# HELPERS
# =========================
def generate_result():
    return random.randint(0, 9)

def time_left():
    delta = current_round["ends_at"] - datetime.utcnow()
    seconds = max(0, int(delta.total_seconds()))
    return seconds // 60, seconds % 60

def check_and_close_round():
    if current_round["result"] is not None:
        return None

    if datetime.utcnow() < current_round["ends_at"]:
        return None

    result = generate_result()
    current_round["result"] = result

    winners = 0
    for uid, g in guesses.items():
        if g == result:
            users[uid] = users.get(uid, 0) + POINTS_PER_WIN
            winners += 1

    guesses.clear()
    current_round["round_id"] += 1
    current_round["ends_at"] = datetime.utcnow() + timedelta(minutes=ROUND_DURATION_MINUTES)
    current_round["result"] = None

    return result, winners

def number_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("0", callback_data="0"),
            InlineKeyboardButton("1", callback_data="1"),
            InlineKeyboardButton("2", callback_data="2"),
            InlineKeyboardButton("3", callback_data="3"),
            InlineKeyboardButton("4", callback_data="4"),
        ],
        [
            InlineKeyboardButton("5", callback_data="5"),
            InlineKeyboardButton("6", callback_data="6"),
            InlineKeyboardButton("7", callback_data="7"),
            InlineKeyboardButton("8", callback_data="8"),
            InlineKeyboardButton("9", callback_data="9"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

# =========================
# COMMANDS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users.setdefault(update.effective_user.id, 0)

    closed = check_and_close_round()
    if closed:
        result, winners = closed
        await update.message.reply_text(
            f"🏁 Previous round closed\n🎯 Result: {result}\n🏆 Winners: {winners}"
        )

    m, s = time_left()
    await update.message.reply_text(
        "🎮 Number Guess Game (Points Only)\n\n"
        "⚠️ Free game. No money. Random system.\n\n"
        f"🆔 Round: {current_round['round_id']}\n"
        f"⏳ Time left: {m}m {s}s\n\n"
        "Choose ONE number (0–9):",
        reply_markup=number_keyboard(),
    )

async def points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pts = users.get(update.effective_user.id, 0)
    await update.message.reply_text(f"⭐ Your points: {pts}")

async def close_round(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Not authorized.")
        return

    closed = check_and_close_round()
    if not closed:
        await update.message.reply_text("⏳ Round still running.")
        return

    result, winners = closed
    await update.message.reply_text(
        f"🔒 Round closed manually\n🎯 Result: {result}\n🏆 Winners: {winners}"
    )

# =========================
# BUTTON HANDLER
# =========================
async def button_play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    users.setdefault(user_id, 0)

    closed = check_and_close_round()
    if closed:
        result, winners = closed
        await query.message.reply_text(
            f"🏁 Round closed\n🎯 Result: {result}\n🏆 Winners: {winners}"
        )
        return

    if user_id in guesses:
        await query.edit_message_text("❗ You already played this round.")
        return

    guess = int(query.data)
    guesses[user_id] = guess

    await query.edit_message_text(
        f"✅ Your guess **{guess}** is locked.\n\n"
        f"🆔 Round: {current_round['round_id']}"
    )

# =========================
# APP START
# =========================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("points", points))
app.add_handler(CommandHandler("close", close_round))
app.add_handler(CallbackQueryHandler(button_play))

print("🤖 Bot is running...")
app.run_polling()

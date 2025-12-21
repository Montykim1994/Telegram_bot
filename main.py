from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)
import os
import random
from datetime import datetime, timedelta

# =============================
# CONFIG
# =============================
TOKEN = os.getenv("BOT_TOKEN")  # Set this in Railway
ADMIN_ID = 891656290            # Your Telegram ID
ROUND_DURATION_MINUTES = 10
POINTS_PER_WIN = 10

# =============================
# GAME STATE (in-memory)
# =============================
users = {}        # user_id -> points
guesses = {}      # user_id -> guessed number

current_round = {
    "round_id": 1,
    "ends_at": datetime.utcnow() + timedelta(minutes=ROUND_DURATION_MINUTES),
}

# ==============================
# KEYBOARD (0–9)
# ==============================
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
        ]
    ]
    return InlineKeyboardMarkup(keyboard)
# =============================
# COMMANDS
# =============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users.setdefault(update.effective_user.id, 0)
    m, s = time_left()

    await update.message.reply_text(
        "🎮 Number Guess Game (Points Mode)\n\n"
        "⚠️ DISCLAIMER\n"
        "• Free game\n"
        "• No money\n"
        "• Random system result\n\n"
        f"🆔 Round ID: {current_round['round_id']}\n"
        f"⏳ Time left: {m}m {s}s\n\n"
        "Commands:\n"
        "/play <00-99>\n"
        "/points\n"
        "/result (admin only)"
    )

# ==============================
# BUTTON HANDLER (0–9)
# ==============================
async def button_play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    users.setdefault(user_id, 0)

    if user_id in guesses:
        await query.edit_message_text("❗ You already played this round.")
        return

    data = query.data  # example: play_7
    guess = int(data.split("_")[1])

    guesses[user_id] = guess

    await query.edit_message_text(
        f"✅ Your guess **{guess}** is locked!\n"
        "Wait for the result ⏳"
    )

async def points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pts = users.get(update.effective_user.id, 0)
    await update.message.reply_text(f"⭐ Your points: {pts}")

async def result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only admin can declare result
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("ℹ️ Result not declared yet.")
        return

    result = generate_result()
    winners = 0

    for uid, g in guesses.items():
        if g == result:
            users[uid] += POINTS_PER_WIN
            winners += 1

    guesses.clear()
    current_round["round_id"] += 1
    current_round["ends_at"] = datetime.utcnow() + timedelta(minutes=ROUND_DURATION_MINUTES)

    await update.message.reply_text(
        f"🏁 RESULT DECLARED\n\n"
        f"🎯 Number: {result:02d}\n"
        f"🏆 Winners: {winners}\n\n"
        f"🆕 New round started!"
    )

# =============================
# APP START
# =============================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("play", play))
app.add_handler(CommandHandler("points", points))
app.add_handler(CommandHandler("result", result))

print("🤖 Bot is running...")
app.run_polling()

import logging
from datetime import date
import psycopg2

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG =================

BOT_TOKEN = "YOUR_BOT_TOKEN"
ADMIN_ID = 123456789        # <-- YOUR Telegram ID
MIN_ADD_POINTS = 50

logging.basicConfig(level=logging.INFO)

# ================= DATABASE =================

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

# ================= START =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
        (user_id,)
    )
    conn.commit()
    conn.close()

    keyboard = [
        [InlineKeyboardButton("➕ Add Points", callback_data="add_points")]
    ]

    if user_id == ADMIN_ID:
        keyboard.append(
            [InlineKeyboardButton("🛠 Admin Panel", callback_data="admin_panel")]
        )

    if update.message:
        await update.message.reply_text(
            "Welcome!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.callback_query.message.edit_text(
            "Welcome!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ================= USER ADD FLOW =================

async def handle_add_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.clear()
    context.user_data["awaiting_amount"] = True

    await query.message.edit_text(
        f"Enter amount to add\nMinimum: {MIN_ADD_POINTS}"
    )

async def process_add_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_amount"):
        return

    text = update.message.text.strip()

    if not text.isdigit():
        return await update.message.reply_text("❌ Enter a valid number")

    amount = int(text)

    if amount < MIN_ADD_POINTS:
        return await update.message.reply_text(
            f"❌ Minimum add is {MIN_ADD_POINTS}"
        )

    context.user_data.clear()
    context.user_data["awaiting_screenshot"] = True
    context.user_data["temp_amount"] = amount

    await update.message.reply_text("📸 Send payment screenshot")

async def process_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_screenshot"):
        return

    if not update.message.photo:
        return await update.message.reply_text("❌ Send a valid photo")

    user_id = update.effective_user.id
    amount = context.user_data["temp_amount"]
    photo_id = update.message.photo[-1].file_id

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO add_requests (user_id, amount, screenshot_id)
        VALUES (%s, %s, %s)
        RETURNING id
    """, (user_id, amount, photo_id))
    req_id = cur.fetchone()[0]
    conn.commit()
    conn.close()

    context.user_data.clear()

    await update.message.reply_text("📤 Request sent to admin")

    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=photo_id,
        caption=(
            "📩 Add Request Received\n\n"
            f"ID: {req_id}\n"
            f"User: {user_id}\n"
            f"Amount: {amount}\n\n"
            "➡️ Use Admin → Manual Add"
        )
    )

# ================= ADMIN PANEL =================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return await query.message.edit_text("❌ Unauthorized")

    keyboard = [
        [
            InlineKeyboardButton("➕ Manual Add", callback_data="admin_manual_add"),
            InlineKeyboardButton("📊 Stats", callback_data="admin_stats")
        ],
        [InlineKeyboardButton("⬅ Back", callback_data="back")]
    ]

    await query.message.edit_text(
        "🛠 Admin Panel",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================= ADMIN MANUAL ADD =================

async def admin_manual_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.clear()
    context.user_data["admin_manual_add"] = True

    await query.message.edit_text(
        "Send:\nUSER_ID AMOUNT\n\nExample:\n123456789 500"
    )

async def process_admin_manual_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("admin_manual_add"):
        return

    if update.effective_user.id != ADMIN_ID:
        return

    parts = update.message.text.split()

    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return await update.message.reply_text("❌ Invalid format")

    user_id, amount = map(int, parts)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET wallet = wallet + %s WHERE user_id=%s",
        (amount, user_id)
    )
    conn.commit()
    conn.close()

    context.user_data.clear()

    await update.message.reply_text("✅ Points added")
    await context.bot.send_message(
        user_id,
        f"💰 {amount} points added by admin"
    )

# ================= ADMIN STATS =================

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM add_requests")
    total_count, total_amt = cur.fetchone()

    cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM add_requests WHERE created=%s",
        (date.today(),)
    )
    today_count, today_amt = cur.fetchone()

    conn.close()

    await query.message.edit_text(
        f"📊 Stats\n\n"
        f"Today: {today_count} adds / {today_amt} points\n"
        f"Total: {total_count} adds / {total_amt} points"
    )

# ================= CALLBACK ROUTER =================

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data

    if data == "add_points":
        return await handle_add_points(update, context)

    if data == "admin_panel":
        return await admin_panel(update, context)

    if data == "admin_manual_add":
        return await admin_manual_add(update, context)

    if data == "admin_stats":
        return await admin_stats(update, context)

    if data == "back":
        return await start(update.callback_query, context)

# ================= TEXT ROUTER =================

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("admin_manual_add"):
        return await process_admin_manual_add(update, context)
    else:
        return await process_add_amount(update, context)

# ================= MAIN =================

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_router))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_router
        )
    )

    app.add_handler(
        MessageHandler(filters.PHOTO, process_screenshot)
    )

    print("🚀 BOT RUNNING")
    app.run_polling()

if __name__ == "__main__":
    main()

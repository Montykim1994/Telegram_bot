import os
import asyncio
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta, date, time
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, JobQueue
)

# =====================================================
# CONFIGURATION
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
POSTGRES_URL = os.getenv("POSTGRES_URL")

ADMIN_ID = 891656290   # <-- YOUR TELEGRAM ID

# Maximum limits
MAX_BETS_PER_BAAJI = 10
MIN_BET = 5
MAX_BET = 5000
MIN_ADD_POINTS = 50

# =====================================================
# DATABASE CONNECTION
# =====================================================

def get_db():
    """Create a new PostgreSQL Connection"""
    return psycopg2.connect(POSTGRES_URL, sslmode="require")


# =====================================================
# INITIALIZE TABLES
# =====================================================

def init_db():
    conn = get_db()
    cur = conn.cursor()

    # Users table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            wallet INT DEFAULT 0,
            last_redeem DATE
        );
    """)

    # Baaji table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS baaji (
            id SERIAL PRIMARY KEY,
            date DATE,
            baaji_number INT,
            patti_result INT,
            single_result INT,
            status VARCHAR(20),
            close_time TIMESTAMP
        );
    """)

    # Bets table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            baaji_id INT,
            type VARCHAR(20),
            digit INT,
            amount INT,
            timestamp TIMESTAMP DEFAULT NOW(),
            FOREIGN KEY (baaji_id) REFERENCES baaji(id)
        );
    """)

    conn.commit()
    conn.close()
    # =====================================================
# BAAJI TIMING & SCHEDULER FUNCTIONS
# =====================================================

# Fixed daily baaji closing times (24-hour format)
BAAJI_CLOSE_TIMES = [
    time(10, 20),   # 1st
    time(11, 50),   # 2nd
    time(13, 20),   # 3rd
    time(14, 55),   # 4th
    time(16, 20),   # 5th
    time(17, 50),   # 6th
    time(19, 20),   # 7th
    time(20, 50)    # 8th
]


def get_current_baaji():
    """Get the currently open baaji or None."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT * FROM baaji
        WHERE status = 'open'
        ORDER BY id DESC LIMIT 1;
    """)

    row = cur.fetchone()
    conn.close()
    return row


def create_new_baaji():
    """Open the next baaji of the day automatically."""
    today = date.today()

    conn = get_db()
    cur = conn.cursor()

    # Count baajis already created today
    cur.execute("SELECT COUNT(*) FROM baaji WHERE date=%s;", (today,))
    count = cur.fetchone()[0]

    if count >= 8:
        conn.close()
        return None  # All baajis for today completed

    baaji_number = count + 1

    close_time = datetime.combine(today, BAAJI_CLOSE_TIMES[count])

    cur.execute("""
        INSERT INTO baaji (date, baaji_number, status, close_time)
        VALUES (%s, %s, %s, %s)
        RETURNING id;
    """, (today, baaji_number, "open", close_time))

    new_id = cur.fetchone()[0]
    conn.commit()
    conn.close()

    return new_id


async def announce_new_baaji(context: ContextTypes.DEFAULT_TYPE, baaji_number):
    """Send a broadcast when new baaji opens."""
    msg = (
        f"🎯 *{baaji_number}ᵗʰ Baaji OPEN!*\n"
        f"Place your bets now! Maximum 10 bets allowed.\n"
        f"Closing Time: {BAAJI_CLOSE_TIMES[baaji_number-1].strftime('%I:%M %p')}"
    )

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users;")
    users = cur.fetchall()
    conn.close()

    for row in users:
        try:
            await context.bot.send_message(chat_id=row[0], text=msg, parse_mode="Markdown")
        except:
            pass


async def auto_close_baaji(context: ContextTypes.DEFAULT_TYPE):
    """Automatically close any active baaji when close_time passes."""
    now = datetime.now()

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT * FROM baaji
        WHERE status='open' AND close_time <= NOW();
    """)

    to_close = cur.fetchall()

    for b in to_close:
        # Mark as closed
        cur.execute("UPDATE baaji SET status='closed' WHERE id=%s;", (b["id"],))
        conn.commit()

        # Broadcast closing msg
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"⏳ Baaji {b['baaji_number']} closed. Waiting for result."
            )
        except:
            pass

        # Broadcast to users
        cur.execute("SELECT user_id FROM users;")
        users = cur.fetchall()
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u[0],
                    text=f"⏳ Betting closed for Baaji {b['baaji_number']}.\nWaiting for result…"
                )
            except:
                pass

    conn.close()


async def midnight_reset(context: ContextTypes.DEFAULT_TYPE):
    """Reset baaji count automatically at midnight."""
    now = datetime.now().time()
    if now.hour == 0 and now.minute < 2:
        # Delete future baajis & open first baaji for new day
        conn = get_db()
        cur = conn.cursor()
        today = date.today()

        cur.execute("DELETE FROM baaji WHERE date=%s;", (today,))
        conn.commit()
        conn.close()

        first_id = create_new_baaji()
        if first_id:
            await announce_new_baaji(context, 1)
            # =====================================================
# USER REGISTRATION & WALLET SYSTEM
# =====================================================

async def ensure_user(update: Update):
    """Make sure user exists in DB before any action."""
    user_id = update.effective_user.id
    conn = get_db()
    cur = conn.cursor()

    cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT DO NOTHING;", (user_id,))
    conn.commit()
    conn.close()


async def wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user wallet balance and wallet menu buttons."""
    await ensure_user(update)
    user_id = update.effective_user.id

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT wallet FROM users WHERE user_id=%s;", (user_id,))
    balance = cur.fetchone()[0]
    conn.close()

    keyboard = [
        [InlineKeyboardButton("➕ Add Points", callback_data="add_points")],
        [InlineKeyboardButton("➖ Redeem Points", callback_data="redeem_points")],
        [InlineKeyboardButton("📜 Bet History", callback_data="bet_history")],
        [InlineKeyboardButton("⬅ Main Menu", callback_data="main_menu")]
    ]

    await update.message.reply_text(
        f"💰 *Your Wallet: {balance} points*\n\n"
        f"Minimum Add: {MIN_ADD_POINTS}\n"
        f"Redeem allowed: Once per day",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =====================================================
# ADD POINTS (User Request)
# =====================================================

async def handle_add_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User taps Add Points button."""
    user_id = update.callback_query.from_user.id
    await update.callback_query.answer()

    await update.callback_query.message.reply_text(
        "💳 *ADD POINTS*\n\n"
        "Send the amount you want to add.\n"
        f"Minimum: {MIN_ADD_POINTS} points.\n\n"
        "_Admin will approve and credit manually._",
        parse_mode="Markdown"
    )

    context.user_data["awaiting_add_amount"] = True


async def process_add_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User sends amount to add, forward to admin for approval."""
    if not context.user_data.get("awaiting_add_amount"):
        return

    user_id = update.effective_user.id
    amount_text = update.message.text

    if not amount_text.isdigit():
        return await update.message.reply_text("❌ Enter a valid number")

    amount = int(amount_text)

    if amount < MIN_ADD_POINTS:
        return await update.message.reply_text(
            f"❌ Minimum add amount is {MIN_ADD_POINTS} points."
        )

    # Forward request to admin
    await update.message.reply_text(
        "📨 Your add-points request has been sent to admin.\n"
        "Please wait for approval."
    )

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"📥 *Add Points Request*\n"
            f"User: `{user_id}`\n"
            f"Amount: `{amount}`\n\n"
            f"Use: /addpoints {user_id} {amount}"
        ),
        parse_mode="Markdown"
    )

    context.user_data["awaiting_add_amount"] = False


# =====================================================
# REDEEM POINTS (User Request)
# =====================================================

async def handle_redeem_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User taps Redeem Points."""
    await update.callback_query.answer()
    user_id = update.callback_query.from_user.id

    conn = get_db()
    cur = conn.cursor()

    # Check last redeem date
    cur.execute("SELECT wallet, last_redeem FROM users WHERE user_id=%s;", (user_id,))
    wallet, last_redeem = cur.fetchone()

    today = date.today()
    if last_redeem == today:
        return await update.callback_query.message.reply_text(
            "❌ You have already redeemed today.\nTry again tomorrow."
        )

    await update.callback_query.message.reply_text(
        "💵 *REDEEM POINTS*\n\n"
        "Send the amount you want to redeem.",
        parse_mode="Markdown"
    )

    context.user_data["awaiting_redeem_amount"] = True


async def process_redeem_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user redeem request."""
    if not context.user_data.get("awaiting_redeem_amount"):
        return

    user_id = update.effective_user.id
    amount_text = update.message.text

    if not amount_text.isdigit():
        return await update.message.reply_text("❌ Enter a valid number.")

    amount = int(amount_text)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT wallet FROM users WHERE user_id=%s;", (user_id,))
    wallet = cur.fetchone()[0]

    if amount > wallet:
        conn.close()
        return await update.message.reply_text("❌ Not enough balance.")

    # Notify admin for approval
    await update.message.reply_text("📨 Redeem request sent to admin.")

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"📤 *Redeem Request*\n"
            f"User: `{user_id}`\n"
            f"Amount: `{amount}`\n\n"
            f"Use: /deductpoints {user_id} {amount}"
        ),
        parse_mode="Markdown"
    )

    conn.close()
    context.user_data["awaiting_redeem_amount"] = False
    # =====================================================
# BETTING SYSTEM — SINGLE & PATTI
# =====================================================

async def play_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show game type options: Single or Patti."""
    await ensure_user(update)

    keyboard = [
        [InlineKeyboardButton("1️⃣ Single Digit", callback_data="play_single")],
        [InlineKeyboardButton("3️⃣ Patti", callback_data="play_patti")],
        [InlineKeyboardButton("🎯 Current Baaji Status", callback_data="baaji_status")],
        [InlineKeyboardButton("⬅ Main Menu", callback_data="main_menu")]
    ]

    await update.message.reply_text(
        "🎮 *SELECT GAME TYPE*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =====================================================
# SINGLE DIGIT — SELECT NUMBER
# =====================================================

async def handle_single(update: Update, context):
    """Show number buttons 0–9."""
    await update.callback_query.answer()

    keyboard = [
        [
            InlineKeyboardButton(str(i), callback_data=f"single_digit_{i}")
            for i in range(0, 5)
        ],
        [
            InlineKeyboardButton(str(i), callback_data=f"single_digit_{i}")
            for i in range(5, 10)
        ],
        [InlineKeyboardButton("⬅ Back", callback_data="play_menu")]
    ]

    await update.callback_query.message.reply_text(
        "🔢 *Choose your single digit (0–9)*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_single_digit(update: Update, context):
    """User clicks a single digit button."""
    await update.callback_query.answer()
    data = update.callback_query.data
    digit = int(data.split("_")[2])

    context.user_data["pending_bet_type"] = "single"
    context.user_data["pending_digit"] = digit

    await update.callback_query.message.reply_text(
        f"💰 Enter bet amount for digit *{digit}* (5–5000):",
        parse_mode="Markdown"
    )

    context.user_data["awaiting_bet_amount"] = True


# =====================================================
# PATTI — ENTER 3-DIGIT NUMBER
# =====================================================

async def handle_patti(update: Update, context):
    """Ask user to enter 3-digit patti."""
    await update.callback_query.answer()

    context.user_data["pending_bet_type"] = "patti"

    await update.callback_query.message.reply_text(
        "🎰 *Enter your Patti (000–999):*",
        parse_mode="Markdown"
    )

    context.user_data["awaiting_patti_input"] = True


async def process_patti_input(update: Update, context):
    """User enters Patti digits."""
    if not context.user_data.get("awaiting_patti_input"):
        return

    patti = update.message.text.strip()

    if not patti.isdigit() or not (0 <= int(patti) <= 999):
        return await update.message.reply_text("❌ Enter a valid Patti between 000–999")

    context.user_data["pending_digit"] = int(patti)
    context.user_data["awaiting_patti_input"] = False

    await update.message.reply_text(
        f"💰 Enter bet amount for Patti *{patti}* (5–5000):",
        parse_mode="Markdown"
    )

    context.user_data["awaiting_bet_amount"] = True


# =====================================================
# PROCESS BET AMOUNT + VALIDATION
# =====================================================

async def process_bet_amount(update: Update, context):
    """Handles amount validation and then shows PLACE/CANCEL buttons."""
    if not context.user_data.get("awaiting_bet_amount"):
        return

    amount_text = update.message.text
    if not amount_text.isdigit():
        return await update.message.reply_text("❌ Enter a valid number.")

    amount = int(amount_text)

    if amount < MIN_BET:
        return await update.message.reply_text(f"❌ Minimum bet is {MIN_BET}")

    if amount > MAX_BET:
        return await update.message.reply_text(f"❌ Maximum bet is {MAX_BET}")

    # Save temporary bet amount
    context.user_data["pending_amount"] = amount
    context.user_data["awaiting_bet_amount"] = False

    # Show confirmation menu
    bet_type = context.user_data["pending_bet_type"]
    digit = context.user_data["pending_digit"]

    confirm_text = (
        f"📝 *Confirm Your Bet*\n\n"
        f"Type: `{bet_type}`\n"
        f"Digit: `{digit}`\n"
        f"Amount: `{amount}`\n"
    )

    keyboard = [
        [InlineKeyboardButton("🟢 PLACE BET", callback_data="confirm_bet")],
        [InlineKeyboardButton("🔴 CANCEL", callback_data="cancel_bet")]
    ]

    await update.message.reply_text(
        confirm_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =====================================================
# PLACE BET FINALIZATION
# =====================================================

async def place_bet_final(update: Update, context):
    """User clicks PLACE BET — deduct wallet, save bet."""
    await update.callback_query.answer()
    user_id = update.callback_query.from_user.id

    bet_type = context.user_data.get("pending_bet_type")
    digit = context.user_data.get("pending_digit")
    amount = context.user_data.get("pending_amount")

    if bet_type is None or digit is None or amount is None:
        return await update.callback_query.message.reply_text("❌ Bet error.")

    # Check for active baaji
    baaji = get_current_baaji()
    if not baaji:
        return await update.callback_query.message.reply_text("❌ No active Baaji available.")

    baaji_id = baaji["id"]

    # Check wallet balance
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT wallet FROM users WHERE user_id=%s;", (user_id,))
    wallet = cur.fetchone()[0]

    if wallet < amount:
        conn.close()
        return await update.callback_query.message.reply_text("❌ Not enough balance.")

    # Check max bets for this baaji
    cur.execute(
        "SELECT COUNT(*) FROM bets WHERE user_id=%s AND baaji_id=%s;",
        (user_id, baaji_id)
    )
    bet_count = cur.fetchone()[0]

    if bet_count >= MAX_BETS_PER_BAAJI:
        conn.close()
        return await update.callback_query.message.reply_text(
            f"❌ Maximum {MAX_BETS_PER_BAAJI} bets allowed per Baaji."
        )

    # Deduct wallet
    new_wallet = wallet - amount
    cur.execute("UPDATE users SET wallet=%s WHERE user_id=%s;", (new_wallet, user_id))

    # Save bet
    cur.execute(
        "INSERT INTO bets (user_id, baaji_id, type, digit, amount) VALUES (%s, %s, %s, %s, %s);",
        (user_id, baaji_id, bet_type, digit, amount)
    )

    conn.commit()
    conn.close()

    # Confirmation message
    await update.callback_query.message.reply_text(
        f"✅ Bet Placed!\nDigit: {digit}\nAmount: {amount}\nNew Wallet: {new_wallet}"
    )

    # Reset pending states
    context.user_data["pending_bet_type"] = None
    context.user_data["pending_digit"] = None
    context.user_data["pending_amount"] = None


# =====================================================
# CANCEL BET
# =====================================================

async def cancel_bet(update: Update, context):
    await update.callback_query.answer()
    context.user_data["pending_bet_type"] = None
    context.user_data["pending_digit"] = None
    context.user_data["pending_amount"] = None

    await update.callback_query.message.reply_text("❌ Bet cancelled.")
    # =====================================================
# ADMIN PANEL COMMANDS
# =====================================================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin controls."""

    # Check admin
    if update.effective_user.id != ADMIN_ID:
        return await update.callback_query.answer("Access denied ❌", show_alert=True)

    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("📌 Set Result", callback_data="admin_set_result")],
        [InlineKeyboardButton("➖ Close Baji", callback_data="admin_close_baji")],
        [InlineKeyboardButton("🚀 Open Next Baji", callback_data="admin_open_next_baji")],
        [InlineKeyboardButton("💰 Add Points", callback_data="admin_add_points")],
        [InlineKeyboardButton("🪙 Deduct Points", callback_data="admin_deduct_points")],
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
    ]

    await query.edit_message_text(
        "🔧 *ADMIN PANEL*\nChoose an option:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =====================================================
# CLOSE BAAJI NOW (Admin override)
# =====================================================

async def admin_close_baaji(update: Update, context):
    """Admin manually closes the current baaji."""
    await update.callback_query.answer()
    user_id = update.callback_query.from_user.id

    if user_id != ADMIN_ID:
        return await update.callback_query.message.reply_text("❌ Unauthorized.")

    baaji = get_current_baaji()
    if not baaji:
        return await update.callback_query.message.reply_text("❌ No active baaji.")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE baaji SET status='closed' WHERE id=%s;", (baaji["id"],))
    conn.commit()
    conn.close()

    await update.callback_query.message.reply_text(
        f"⛔ Baaji {baaji['baaji_number']} closed manually."
    )


# =====================================================
# ADMIN — ENTER RESULT FLOW
# =====================================================

async def admin_set_result_start(update: Update, context):
    """Admin chooses to enter result."""
    await update.callback_query.answer()

    if update.callback_query.from_user.id != ADMIN_ID:
        return await update.callback_query.message.reply_text("❌ Unauthorized.")

    baaji = get_current_baaji()

    if not baaji:
        return await update.callback_query.message.reply_text("❌ No baaji awaiting result.")

    await update.callback_query.message.reply_text(
        f"🎯 *Enter 3-digit Patti result for Baaji {baaji['baaji_number']}*\n"
        f"Example: 578",
        parse_mode="Markdown"
    )

    context.user_data["awaiting_admin_result"] = True


async def admin_process_result(update: Update, context):
    """Admin sends patti result (3-digit)."""
    if not context.user_data.get("awaiting_admin_result"):
        return

    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return await update.message.reply_text("❌ Unauthorized.")

    patti = update.message.text.strip()
    if not patti.isdigit() or not (0 <= int(patti) <= 999):
        return await update.message.reply_text("❌ Enter valid patti between 000–999.")

    patti_value = int(patti)
    single_value = sum(map(int, patti.zfill(3))) % 10  # auto single calc

    baaji = get_current_baaji()
    if not baaji:
        return await update.message.reply_text("❌ No active baaji.")

    baaji_id = baaji["id"]

    conn = get_db()
    cur = conn.cursor()

    # Save results
    cur.execute(
        "UPDATE baaji SET patti_result=%s, single_result=%s, status='resulted' WHERE id=%s;",
        (patti_value, single_value, baaji_id)
    )

    # Fetch bets for this baaji
    cur.execute(
        "SELECT user_id, type, digit, amount FROM bets WHERE baaji_id=%s;",
        (baaji_id,)
    )
    all_bets = cur.fetchall()

    winners = {}

    # Calculate winners
    for user_id, bet_type, bet_digit, amount in all_bets:
        if bet_type == "single" and bet_digit == single_value:
            win_amount = amount * 9
        elif bet_type == "patti" and bet_digit == patti_value:
            win_amount = amount * 90
        else:
            win_amount = 0

        if win_amount > 0:
            winners[user_id] = winners.get(user_id, 0) + win_amount

    # Update wallets
    for user_id, win_amount in winners.items():
        cur.execute(
            "UPDATE users SET wallet = wallet + %s WHERE user_id=%s;",
            (win_amount, user_id)
        )

    conn.commit()
    conn.close()

    # Notify winners
    for user_id, win_amount in winners.items():
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🏆 Congratulations!\nYou won {win_amount} points in Baaji {baaji['baaji_number']}!"
            )
        except:
            pass

    # Broadcast result to all users
    await broadcast_result(context, baaji["baaji_number"], patti_value, single_value)

    # Open next baaji automatically
    await open_next_baaji(context, baaji["baaji_number"])

    context.user_data["awaiting_admin_result"] = False


# =====================================================
# BROADCAST RESULT TO ALL USERS
# =====================================================

async def broadcast_result(context, baaji_no, patti, single):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users;")
    users = cur.fetchall()
    conn.close()

    msg = (
        f"🎉 *RESULT DECLARED!*\n\n"
        f"Baaji: {baaji_no}\n"
        f"🔢 Single: {single}\n"
        f"🎰 Patti: {str(patti).zfill(3)}\n"
        f"🏆 Winners credited!"
    )

    for row in users:
        try:
            await context.bot.send_message(chat_id=row[0], text=msg, parse_mode="Markdown")
        except:
            pass


# =====================================================
# OPEN NEXT BAAJI
# =====================================================

async def open_next_baaji(context, prev_baaji_no):
    """Open next baaji immediately after result is declared."""
    if prev_baaji_no >= 8:
        # End of day
        return

    new_id = create_new_baaji()

    if new_id:
        await announce_new_baaji(context, prev_baaji_no + 1)
        # =====================================================
# RESULTS DISPLAY (TODAY / YESTERDAY / PREVIOUS)
# =====================================================

async def results_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show results menu."""
    keyboard = [
        [InlineKeyboardButton("📅 Today's Results", callback_data="results_today")],
        [InlineKeyboardButton("📅 Yesterday's Results", callback_data="results_yesterday")],
        [InlineKeyboardButton("📅 Previous Results", callback_data="results_previous")],
        [InlineKeyboardButton("⬅ Main Menu", callback_data="main_menu")]
    ]

    await update.message.reply_text(
        "📊 *RESULTS MENU*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =====================================================
# TODAY'S RESULTS
# =====================================================

async def results_today(update: Update, context):
    await update.callback_query.answer()

    today = date.today()

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT baaji_number, patti_result, single_result
        FROM baaji
        WHERE date=%s
        ORDER BY baaji_number ASC;
    """, (today,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return await update.callback_query.message.reply_text(
            "❌ No results for today yet."
        )

    patti_row = " | ".join(str(r["patti_result"]).zfill(3) if r["patti_result"] is not None else "---" for r in rows)
    single_row = " | ".join(str(r["single_result"]) if r["single_result"] is not None else "-" for r in rows)

    msg = (
        f"📅 *Today's Results ({today.strftime('%d/%m/%Y')})*\n\n"
        f"{patti_row}\n"
        f"{single_row}"
    )

    await update.callback_query.message.reply_text(msg, parse_mode="Markdown")


# =====================================================
# YESTERDAY'S RESULTS
# =====================================================

async def results_yesterday(update: Update, context):
    await update.callback_query.answer()

    yesterday = date.today() - timedelta(days=1)

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT baaji_number, patti_result, single_result
        FROM baaji
        WHERE date=%s
        ORDER BY baaji_number ASC;
    """, (yesterday,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return await update.callback_query.message.reply_text(
            "❌ No results available for yesterday."
        )

    patti_row = " | ".join(str(r["patti_result"]).zfill(3) if r["patti_result"] is not None else "---" for r in rows)
    single_row = " | ".join(str(r["single_result"]) if r["single_result"] is not None else "-" for r in rows)

    msg = (
        f"📅 *Yesterday's Results ({yesterday.strftime('%d/%m/%Y')})*\n\n"
        f"{patti_row}\n"
        f"{single_row}"
    )

    await update.callback_query.message.reply_text(msg, parse_mode="Markdown")


# =====================================================
# PREVIOUS RESULTS — DATE LIST
# =====================================================

async def results_previous(update: Update, context):
    await update.callback_query.answer()

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT date FROM baaji
        WHERE date < %s
        ORDER BY date DESC LIMIT 10;
    """, (date.today(),))

    dates = cur.fetchall()
    conn.close()

    if not dates:
        return await update.callback_query.message.reply_text("❌ No older results available.")

    keyboard = [
        [InlineKeyboardButton(d[0].strftime("%d/%m/%Y"), callback_data=f"result_date_{d[0]}")]
        for d in dates
    ]
    keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="results_menu")])

    await update.callback_query.message.reply_text(
        "📅 Select a date:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =====================================================
# SHOW RESULTS FOR A SELECTED DATE
# =====================================================

async def show_results_for_date(update: Update, context):
    await update.callback_query.answer()

    data = update.callback_query.data
    date_str = data.replace("result_date_", "")
    selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT baaji_number, patti_result, single_result
        FROM baaji
        WHERE date=%s
        ORDER BY baaji_number ASC;
    """, (selected_date,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return await update.callback_query.message.reply_text(
            "❌ No results for this date."
        )

    patti_row = " | ".join(str(r["patti_result"]).zfill(3) if r["patti_result"] is not None else "---" for r in rows)
    single_row = " | ".join(str(r["single_result"]) if r["single_result"] is not None else "-" for r in rows)

    msg = (
        f"📅 *Results for {selected_date.strftime('%d/%m/%Y')}*\n\n"
        f"{patti_row}\n"
        f"{single_row}"
    )

    await update.callback_query.message.reply_text(msg, parse_mode="Markdown")
    # =====================================================
# MAIN MENU & START COMMAND
# =====================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(update)

    keyboard = [
        [InlineKeyboardButton("🎮 Play", callback_data="play_menu")],
        [InlineKeyboardButton("💰 Wallet", callback_data="wallet_menu")],
        [InlineKeyboardButton("📊 Results", callback_data="results_menu")],
        [InlineKeyboardButton("ℹ️ Rules", callback_data="rules")],
    ]

    if update.effective_user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="admin_panel")])

    await update.message.reply_text(
        "🎉 *Welcome to FF Game Bot!* 🎉\n"
        "Choose an option below:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =====================================================
# RULES MESSAGE
# =====================================================

async def rules(update: Update, context):
    await update.callback_query.answer()

    msg = (
        "📘 *GAME RULES*\n\n"
        "1️⃣ BAJI OPEN/CLOSE SYSTEM\n"
        "• Baaji opens 1 hour before closing time.\n"
        "• Betting closes automatically at fixed time.\n"
        "• Next Baaji opens ONLY after admin declares result.\n\n"
        "2️⃣ BETTING RULES\n"
        "• Bet limit per Baaji: 10 bets\n"
        "• Minimum bet: 5\n"
        "• Maximum bet: 5000\n\n"
        "3️⃣ WALLET RULES\n"
        "• Minimum Add Points: 50\n"
        "• Redeem allowed once per day\n"
        "• Winnings credited automatically\n\n"
        "4️⃣ RESULT RULES\n"
        "• Admin enters 3-digit Patti\n"
        "• Bot auto-calculates single digit\n"
        "• Results broadcast to all users\n\n"
    )

    await update.callback_query.message.reply_text(msg, parse_mode="Markdown")


# =====================================================
# CALLBACK QUERY ROUTER
# =====================================================

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data

    # MAIN MENU ROUTING
    if data == "main_menu":
        return await start(update, context)

    if data == "play_menu":
        return await play_menu(update, context)

    if data == "wallet_menu":
        return await wallet_menu(update, context)

    if data == "results_menu":
        return await results_menu(update, context)

    if data == "rules":
        return await rules(update, context)

    # PLAY ROUTING
    if data == "play_single":
        return await handle_single(update, context)

    if data == "play_patti":
        return await handle_patti(update, context)

    if data.startswith("single_digit_"):
        return await handle_single_digit(update, context)

    if data == "confirm_bet":
        return await place_bet_final(update, context)

    if data == "cancel_bet":
        return await cancel_bet(update, context)

    # WALLET ROUTING
    if data == "add_points":
        return await handle_add_points(update, context)

    if data == "redeem_points":
        return await handle_redeem_points(update, context)

    if data == "bet_history":
        return await show_bet_history(update, context)

    # RESULTS ROUTING
    if data == "results_today":
        return await results_today(update, context)

    if data == "results_yesterday":
        return await results_yesterday(update, context)

    if data == "results_previous":
        return await results_previous(update, context)

    if data.startswith("result_date_"):
        return await show_results_for_date(update, context)

    # ADMIN ROUTING
    if data == "admin_panel":
        return await admin_panel(update, context)

    if data == "admin_close_baaji":
        return await admin_close_baaji(update, context)

    if data == "admin_set_result":
        return await admin_set_result_start(update, context)

    if data == "admin_open_next":
        return await open_next_baaji(context, 0)

    if data == "admin_add_points":
        return await admin_add_points_menu(update, context)

    if data == "admin_deduct_points":
        return await admin_deduct_points_menu(update, context)

    if data == "admin_stats":
        return await admin_stats(update, context)


# =====================================================
# ADMIN ADD/DEDUCT POINTS (TEXT INPUT)
# =====================================================

async def admin_add_points_menu(update: Update, context):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Enter: /addpoints user_id amount"
    )

async def admin_deduct_points_menu(update: Update, context):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Enter: /deductpoints user_id amount"
    )


async def addpoints_cmd(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) != 2:
        return await update.message.reply_text("Use: /addpoints user amount")

    user_id = int(context.args[0])
    amount = int(context.args[1])

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET wallet = wallet + %s WHERE user_id=%s;", (amount, user_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"Added {amount} points to {user_id}")


async def deductpoints_cmd(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) != 2:
        return await update.message.reply_text("Use: /deductpoints user amount")

    user_id = int(context.args[0])
    amount = int(context.args[1])

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET wallet = wallet - %s, last_redeem=%s WHERE user_id=%s;",
                (amount, date.today(), user_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"Deducted {amount} points from {user_id}")


# =====================================================
# SHOW BET HISTORY
# =====================================================

async def show_bet_history(update: Update, context):
    await update.callback_query.answer()
    user_id = update.callback_query.from_user.id

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT b.baaji_id, b.type, b.digit, b.amount
        FROM bets b
        WHERE b.user_id=%s
        ORDER BY id DESC LIMIT 20;
    """, (user_id,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return await update.callback_query.message.reply_text(
            "📜 No bet history found."
        )

    msg = "📜 *Your Last 20 Bets:*\n\n"
    for r in rows:
        msg += f"Baaji {r['baaji_id']} → {r['type']} {r['digit']} | {r['amount']}\n"

    await update.callback_query.message.reply_text(msg, parse_mode="Markdown")


# =====================================================
# REGISTER HANDLERS & START BOT
# =====================================================

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addpoints", addpoints_cmd))
    app.add_handler(CommandHandler("deductpoints", deductpoints_cmd))

    # Callback handler
    app.add_handler(CallbackQueryHandler(callback_router))

    # Text handlers for add/redeem/bet inputs
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_add_amount))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_redeem_amount))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_patti_input))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_bet_amount))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_process_result))

    # Scheduled Jobs
    app.job_queue.run_repeating(auto_close_baaji, interval=30, first=10)
    app.job_queue.run_repeating(midnight_reset, interval=60, first=20)

    # Run
    print("BOT IS RUNNING...")
    app.run_polling()


# =====================================================
# START THE BOT
# =====================================================

if __name__ == "__main__":
    main()
        
    

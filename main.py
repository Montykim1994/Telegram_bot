# =====================================================
# IMPORTS
# =====================================================

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
    MessageHandler, filters, ContextTypes
)


# =====================================================
# CONFIGURATION
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
POSTGRES_URL = os.getenv("POSTGRES_URL")
ADMIN_ID = 891656290  # Your Telegram ID

# Game limits
MAX_BETS_PER_BAAJI = 10
MIN_BET = 5
MAX_BET = 5000
MIN_ADD_POINTS = 50

# Fixed Baaji closing times (24h format)
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


# =====================================================
# DATABASE CONNECTION HELPERS
# =====================================================

def get_db():
    """
    Returns a safe PostgreSQL connection with SSL required.
    """
    return psycopg2.connect(POSTGRES_URL, sslmode="require")


def init_db():
    """
    Initializes required tables if they don't exist.
    """
    conn = get_db()
    cur = conn.cursor()

    # User table
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
# BAAJI HANDLING LOGIC
# =====================================================

def get_current_baaji():
    """
    Get the currently open baaji (if any).
    """
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
    """
    Creates the next baaji of the day when required.
    """
    today = date.today()
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM baaji WHERE date=%s;", (today,))
    count_today = cur.fetchone()[0]

    if count_today >= 8:
        conn.close()
        return None  # All Baajis already created

    baaji_number = count_today + 1
    close_time = datetime.combine(today, BAAJI_CLOSE_TIMES[count_today])

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
    """
    Send broadcast message to all users when a new baaji opens.
    """
    msg = (
        f"🎯 *Baaji {baaji_number} OPEN!*\n"
        f"Place your bets now.\n"
        f"Closes at: {BAAJI_CLOSE_TIMES[baaji_number - 1].strftime('%I:%M %p')}"
    )

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users;")
    users = cur.fetchall()
    conn.close()

    for u in users:
        try:
            await context.bot.send_message(chat_id=u[0], text=msg, parse_mode="Markdown")
        except:
            pass


async def auto_close_baaji(context: ContextTypes.DEFAULT_TYPE):
    """
    Automatically closes any open baaji once its time passes.
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT * FROM baaji
        WHERE status='open' AND close_time <= NOW();
    """)

    expired = cur.fetchall()

    for b in expired:
        cur.execute("UPDATE baaji SET status='closed' WHERE id=%s;", (b["id"],))
        conn.commit()

        # Notify admin
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"⏳ Baaji {b['baaji_number']} closed. Awaiting result."
            )
        except:
            pass

    conn.close()


async def midnight_reset(context: ContextTypes.DEFAULT_TYPE):
    """
    Resets daily Baaji schedule at midnight.
    """
    now = datetime.now().time()

    if now.hour == 0 and now.minute < 2:
        conn = get_db()
        cur = conn.cursor()

        today = date.today()
        cur.execute("DELETE FROM baaji WHERE date=%s;", (today,))
        conn.commit()
        conn.close()

        new_id = create_new_baaji()
        if new_id:
            await announce_new_baaji(context, 1)
# =====================================================
# USER REGISTRATION & WALLET SYSTEM
# =====================================================

async def ensure_user(update: Update):
    """
    Ensures the user exists in the database before any action.
    """
    user_id = update.effective_user.id
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users (user_id)
        VALUES (%s)
        ON CONFLICT DO NOTHING;
    """, (user_id,))

    conn.commit()
    conn.close()


async def wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Shows the user's wallet balance and menu options.
    """
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

    await update.callback_query.message.edit_text(
        f"💰 *Your Wallet: {balance} points*\n\n"
        f"Minimum Add: {MIN_ADD_POINTS}\n"
        f"Redeem allowed once per day.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =====================================================
# ADD POINTS (USER REQUEST)
# =====================================================

async def handle_add_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    User clicked "Add Points" button.
    """
    query = update.callback_query
    await query.answer()

    await query.message.edit_text(
        "💳 *ADD POINTS*\n\n"
        "Send the amount you want to add.\n"
        f"Minimum: {MIN_ADD_POINTS}\n\n"
        "_Admin will approve and credit manually._",
        parse_mode="Markdown"
    )

    # Set state flag
    context.user_data["awaiting_add_amount"] = True

# ============================
# PROCESS ADD AMOUNT
# ============================
async def process_add_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_add_amount"):
        return  # Ignore unrelated messages

    user_id = update.effective_user.id
    amount_text = update.message.text.strip()

    if not amount_text.isdigit():
        return await update.message.reply_text("❌ Enter a valid number.")

    amount = int(amount_text)

    if amount < MIN_ADD_POINTS:
        return await update.message.reply_text(
            f"❌ Minimum add amount is {MIN_ADD_POINTS}."
        )

    # Save amount temporarily
    context.user_data["temp_amount"] = amount
    context.user_data["awaiting_screenshot"] = True
    context.user_data["awaiting_add_amount"] = False

    await update.message.reply_text(
        "📸 Please upload the payment screenshot to verify your payment."
    )


# ============================
# PROCESS SCREENSHOT
# ============================
async def process_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_screenshot"):
        return  # Ignore if not expecting screenshot

    user_id = update.effective_user.id
    amount = context.user_data.get("temp_amount")

    if not update.message.photo:
        return await update.message.reply_text("❌ Please send a valid screenshot (photo).")

    screenshot_id = update.message.photo[-1].file_id

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO add_requests (user_id, amount, screenshot_id, status)
        VALUES (%s, %s, %s, 'pending')
        RETURNING id;
    """, (user_id, amount, screenshot_id))

    request_id = cur.fetchone()[0]
    conn.commit()
    conn.close()

    await update.message.reply_text("📤 Your request has been submitted! Admin will review it.")

    # Send to admin with Approve / Reject buttons
    buttons = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{request_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{request_id}")
        ]
    ]

    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=screenshot_id,
        caption=(
            f"📥 *Add Request Received*\n"
            f"ID: `{request_id}`\n"
            f"User: `{user_id}`\n"
            f"Amount: `{amount}`"
        ),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )

    # Clear temporary flags
    context.user_data["awaiting_screenshot"] = False
    context.user_data["temp_amount"] = None


    

# =====================================================
# REDEEM POINTS (USER REQUEST)
# =====================================================

async def handle_redeem_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    User clicked Redeem Points.
    """
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT wallet, last_redeem FROM users WHERE user_id=%s;", (user_id,))
    wallet, last_redeem = cur.fetchone()
    conn.close()

    today = date.today()
    if last_redeem == today:
        return await query.message.reply_text(
            "⚠️ You already redeemed today.\nTry again tomorrow."
        )

    await query.message.edit_text(
        "💵 *REDEEM POINTS*\n\n"
        "Enter amount to redeem.",
        parse_mode="Markdown"
    )

    context.user_data["awaiting_redeem_amount"] = True


async def process_redeem_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    User sends redeem amount.
    """
    if not context.user_data.get("awaiting_redeem_amount"):
        return

    user_id = update.effective_user.id
    amount_text = update.message.text.strip()

    if not amount_text.isdigit():
        return await update.message.reply_text("❌ Enter a valid number.")

    amount = int(amount_text)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT wallet FROM users WHERE user_id=%s;", (user_id,))
    wallet = cur.fetchone()[0]
    conn.close()

    if amount > wallet:
        return await update.message.reply_text("❌ Not enough balance.")

    # Notify admin
    await update.message.reply_text("📤 Redeem request sent to admin.")

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"📤 *Redeem Request*\n"
            f"User ID: `{user_id}`\n"
            f"Amount: `{amount}`\n\n"
            f"Use:\n"
            f"/deductpoints {user_id} {amount}"
        ),
        parse_mode="Markdown"
    )

    context.user_data["awaiting_redeem_amount"] = False
# =====================================================
# PLAY MENU — GAME TYPE SELECTION
# =====================================================

async def play_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Shows game type options: Single / Patti.
    """
    await ensure_user(update)

    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.message

    keyboard = [
        [InlineKeyboardButton("1️⃣ Single Digit", callback_data="play_single")],
        [InlineKeyboardButton("3️⃣ Patti (3-Digit)", callback_data="play_patti")],
        [InlineKeyboardButton("🎯 Baaji Status", callback_data="baaji_status")],
        [InlineKeyboardButton("⬅ Main Menu", callback_data="main_menu")]
    ]

    await message.reply_text(
        "🎮 *SELECT GAME TYPE*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =====================================================
# SINGLE DIGIT — NUMBER BUTTONS (0–9)
# =====================================================

async def handle_single(update: Update, context):
    """
    Show digit buttons 0–9.
    """
    query = update.callback_query
    await query.answer()

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

    await query.message.edit_text(
        "🔢 *Choose single digit (0–9)*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_single_digit(update: Update, context):
    """
    User selects a single digit. Ask for amount.
    """
    query = update.callback_query
    await query.answer()

    digit = int(query.data.split("_")[2])

    context.user_data["pending_bet_type"] = "single"
    context.user_data["pending_digit"] = digit

    await query.message.edit_text(
        f"💰 Enter your bet amount for digit *{digit}* (5–5000):",
        parse_mode="Markdown"
    )

    context.user_data["awaiting_bet_amount"] = True


# =====================================================
# PATTI (3-DIGIT) INPUT
# =====================================================

async def handle_patti(update: Update, context):
    """
    Ask user for 3-digit Patti.
    """
    query = update.callback_query
    await query.answer()

    context.user_data["pending_bet_type"] = "patti"

    await query.message.edit_text(
        "🎰 *Enter 3-Digit Patti (000–999):*",
        parse_mode="Markdown"
    )

    context.user_data["awaiting_patti_input"] = True


async def process_patti_input(update: Update, context):
    """
    Validate user Patti input.
    """
    if not context.user_data.get("awaiting_patti_input"):
        return  # Ignore messages not part of this flow

    patti = update.message.text.strip()

    if not patti.isdigit() or not (0 <= int(patti) <= 999):
        return await update.message.reply_text(
            "❌ Invalid Patti. Enter a number between 000 and 999."
        )

    context.user_data["pending_digit"] = int(patti)
    context.user_data["awaiting_patti_input"] = False

    await update.message.reply_text(
        f"💰 Enter bet amount for Patti *{patti}* (5–5000):",
        parse_mode="Markdown"
    )

    context.user_data["awaiting_bet_amount"] = True


# =====================================================
# PROCESS BET AMOUNT (VALIDATION)
# =====================================================

async def process_bet_amount(update: Update, context):
    """
    Validate bet amount and show confirmation buttons.
    """
    if not context.user_data.get("awaiting_bet_amount"):
        return  # Ignore unrelated messages

    amount_text = update.message.text.strip()

    if not amount_text.isdigit():
        return await update.message.reply_text("❌ Enter a valid number.")

    amount = int(amount_text)

    if amount < MIN_BET:
        return await update.message.reply_text(
            f"❌ Minimum bet amount is {MIN_BET}."
        )

    if amount > MAX_BET:
        return await update.message.reply_text(
            f"❌ Maximum bet amount is {MAX_BET}."
        )

    # Save amount
    context.user_data["pending_amount"] = amount
    context.user_data["awaiting_bet_amount"] = False

    btype = context.user_data["pending_bet_type"]
    digit = context.user_data["pending_digit"]

    msg = (
        f"📝 *Confirm Bet*\n\n"
        f"Type: `{btype}`\n"
        f"Digit: `{digit}`\n"
        f"Amount: `{amount}`\n"
    )

    keyboard = [
        [InlineKeyboardButton("🟢 PLACE BET", callback_data="confirm_bet")],
        [InlineKeyboardButton("🔴 CANCEL", callback_data="cancel_bet")]
    ]

    await update.message.reply_text(
        msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =====================================================
# PLACE BET FINALIZATION
# =====================================================

async def place_bet_final(update: Update, context):
    """
    Deduct wallet, save bet, confirm success.
    """
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    btype = context.user_data.get("pending_bet_type")
    digit = context.user_data.get("pending_digit")
    amount = context.user_data.get("pending_amount")

    if None in (btype, digit, amount):
        return await query.message.edit_text("❌ Bet information missing.")

    baaji = get_current_baaji()
    if not baaji:
        return await query.message.edit_text("❌ No active Baaji at the moment.")

    baaji_id = baaji["id"]

    conn = get_db()
    cur = conn.cursor()

    # check user's wallet
    cur.execute("SELECT wallet FROM users WHERE user_id=%s;", (user_id,))
    wallet = cur.fetchone()[0]

    if wallet < amount:
        conn.close()
        return await query.message.edit_text("❌ Not enough balance.")

    # check max bets on this baaji
    cur.execute("""
        SELECT COUNT(*) FROM bets
        WHERE user_id=%s AND baaji_id=%s;
    """, (user_id, baaji_id))
    user_bet_count = cur.fetchone()[0]

    if user_bet_count >= MAX_BETS_PER_BAAJI:
        conn.close()
        return await query.message.edit_text(
            f"⚠️ Max {MAX_BETS_PER_BAAJI} bets allowed per Baaji."
        )

    # Deduct wallet, save bet
    new_wallet = wallet - amount

    cur.execute("UPDATE users SET wallet=%s WHERE user_id=%s;",
                (new_wallet, user_id))

    cur.execute("""
        INSERT INTO bets (user_id, baaji_id, type, digit, amount)
        VALUES (%s, %s, %s, %s, %s);
    """, (user_id, baaji_id, btype, digit, amount))

    conn.commit()
    conn.close()

    await query.message.edit_text(
        f"✅ Bet Placed!\nDigit: {digit}\nAmount: {amount}\nNew Wallet: {new_wallet}"
    )

    # clear pending state
    context.user_data["pending_bet_type"] = None
    context.user_data["pending_digit"] = None
    context.user_data["pending_amount"] = None


# =====================================================
# CANCEL BET
# =====================================================

async def cancel_bet(update: Update, context):
    """
    Cancels pending bet and clears state.
    """
    query = update.callback_query
    await query.answer()

    context.user_data["pending_bet_type"] = None
    context.user_data["pending_digit"] = None
    context.user_data["pending_amount"] = None

    await query.message.edit_text("❌ Bet cancelled.")
# =====================================================
# ADMIN — CLOSE BAAJI MANUALLY
# =====================================================

async def admin_close_baaji(update: Update, context):
    """
    Admin manually closes the current Baaji.
    """
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return await query.message.edit_text("❌ Unauthorized.")

    baaji = get_current_baaji()
    if not baaji:
        return await query.message.edit_text("❌ No active Baaji.")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE baaji SET status='closed' WHERE id=%s;", (baaji["id"],))
    conn.commit()
    conn.close()

    await query.message.edit_text(
        f"⛔ Baaji {baaji['baaji_number']} closed manually."
    )


# =====================================================
# ADMIN — SET RESULT (PATTI)
# =====================================================

async def admin_set_result_start(update: Update, context):
    """
    Prompt admin to enter Patti result.
    """
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return await query.message.edit_text("❌ Unauthorized.")

    baaji = get_current_baaji()
    if not baaji:
        return await query.message.edit_text("❌ No Baaji awaiting result.")

    context.user_data["awaiting_admin_result"] = True

    await query.message.edit_text(
        f"🎯 Enter Patti Result (3 digits) for Baaji {baaji['baaji_number']}\n"
        f"Example: 578",
        parse_mode="Markdown"
    )


async def admin_process_result(update: Update, context):
    """
    Admin sends Patti result — process winners.
    """
    if not context.user_data.get("awaiting_admin_result"):
        return

    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return await update.message.reply_text("❌ Unauthorized.")

    patti = update.message.text.strip()

    if not patti.isdigit() or not (0 <= int(patti) <= 999):
        return await update.message.reply_text("❌ Enter valid Patti (000-999).")

    patti_value = int(patti)
    single_value = sum(map(int, patti.zfill(3))) % 10

    baaji = get_current_baaji()
    if not baaji:
        return await update.message.reply_text("❌ No active Baaji.")

    baaji_id = baaji["id"]

    conn = get_db()
    cur = conn.cursor()

    # Save results
    cur.execute("""
        UPDATE baaji SET patti_result=%s, single_result=%s, status='resulted'
        WHERE id=%s;
    """, (patti_value, single_value, baaji_id))

    # Fetch bets for this Baaji
    cur.execute("""
        SELECT user_id, type, digit, amount
        FROM bets WHERE baaji_id=%s;
    """, (baaji_id,))

    all_bets = cur.fetchall()

    winners = {}  # user_id -> win amount

    # Evaluate win amounts
    for user_id, bet_type, bet_digit, amount in all_bets:
        win_amount = 0

        if bet_type == "single" and bet_digit == single_value:
            win_amount = amount * 9
        elif bet_type == "patti" and bet_digit == patti_value:
            win_amount = amount * 90

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

    # Notify winners individually
    for user_id, win_amount in winners.items():
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"🏆 Congratulations!\n"
                    f"You won {win_amount} points in Baaji {baaji['baaji_number']}!"
                )
            )
        except:
            pass

    # Broadcast result to all users
    await broadcast_result(context, baaji["baaji_number"], patti_value, single_value)

    # Open next Baaji
    await open_next_baaji(context, baaji["baaji_number"])

    context.user_data["awaiting_admin_result"] = False


# =====================================================
# BROADCAST RESULT
# =====================================================

async def broadcast_result(context, baaji_no, patti, single):
    """
    Sends result announcement to all users.
    """
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
            await context.bot.send_message(
                chat_id=row[0],
                text=msg,
                parse_mode="Markdown"
            )
        except:
            pass


# =====================================================
# OPEN NEXT BAAJI
# =====================================================

async def open_next_baaji(context, prev_baaji_no):
    """
    Opens the next Baaji immediately after result is declared.
    """
    if prev_baaji_no >= 8:
        return  # Day complete

    new_id = create_new_baaji()
    if new_id:
        await announce_new_baaji(context, prev_baaji_no + 1)


# =====================================================
# RESULTS MENU
# =====================================================

async def results_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Shows results menu (today, yesterday, previous days).
    """
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.message

    keyboard = [
        [InlineKeyboardButton("📅 Today's Results", callback_data="results_today")],
        [InlineKeyboardButton("📅 Yesterday's Results", callback_data="results_yesterday")],
        [InlineKeyboardButton("📅 Previous Results", callback_data="results_previous")],
        [InlineKeyboardButton("⬅ Main Menu", callback_data="main_menu")]
    ]

    await message.reply_text(
        "📊 *RESULTS MENU*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =====================================================
# TODAY'S RESULTS
# =====================================================

async def results_today(update: Update, context):
    query = update.callback_query
    await query.answer()

    today = date.today()

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT baaji_number, patti_result, single_result
        FROM baaji
        WHERE date=%s ORDER BY baaji_number ASC;
    """, (today,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return await query.message.edit_text("❌ No results for today yet.")

    patti_row = " | ".join(
        str(r["patti_result"]).zfill(3) if r["patti_result"] is not None else "---"
        for r in rows
    )
    single_row = " | ".join(
        str(r["single_result"]) if r["single_result"] is not None else "-"
        for r in rows
    )

    msg = (
        f"📅 *Today's Results ({today.strftime('%d/%m/%Y')})*\n\n"
        f"{patti_row}\n"
        f"{single_row}"
    )

    await query.message.edit_text(msg, parse_mode="Markdown")
# =====================================================
# YESTERDAY'S RESULTS
# =====================================================

async def results_yesterday(update: Update, context):
    query = update.callback_query
    await query.answer()

    yesterday = date.today() - timedelta(days=1)

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT baaji_number, patti_result, single_result
        FROM baaji
        WHERE date=%s ORDER BY baaji_number ASC;
    """, (yesterday,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return await query.message.edit_text("❌ No results for yesterday.")

    patti_row = " | ".join(
        str(r["patti_result"]).zfill(3) if r["patti_result"] is not None else "---"
        for r in rows
    )
    single_row = " | ".join(
        str(r["single_result"]) if r["single_result"] is not None else "-"
        for r in rows
    )

    msg = (
        f"📅 *Yesterday's Results ({yesterday.strftime('%d/%m/%Y')})*\n\n"
        f"{patti_row}\n"
        f"{single_row}"
    )

    await query.message.edit_text(msg, parse_mode="Markdown")


# =====================================================
# PREVIOUS RESULTS — DATE LIST
# =====================================================

async def results_previous(update: Update, context):
    query = update.callback_query
    await query.answer()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT date
        FROM baaji
        WHERE date < %s
        ORDER BY date DESC LIMIT 10;
    """, (date.today(),))

    dates = cur.fetchall()
    conn.close()

    if not dates:
        return await query.message.edit_text("❌ No earlier results available.")

    keyboard = [
        [InlineKeyboardButton(
            d[0].strftime("%d/%m/%Y"),
            callback_data=f"result_date_{d[0].isoformat()}"
        )]
        for d in dates
    ]
    keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="results_menu")])

    await query.message.edit_text(
        "📅 Select a date:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =====================================================
# SHOW RESULTS FOR SELECTED DATE
# =====================================================

async def show_results_for_date(update: Update, context):
    query = update.callback_query
    await query.answer()

    cb_data = query.data
    date_str = cb_data.replace("result_date_", "")
    selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("""
        SELECT baaji_number, patti_result, single_result
        FROM baaji
        WHERE date=%s ORDER BY baaji_number ASC;
    """, (selected_date,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return await query.message.edit_text("❌ No results for this date.")

    patti_row = " | ".join(
        str(r["patti_result"]).zfill(3) if r["patti_result"] else "---"
        for r in rows
    )
    single_row = " | ".join(
        str(r["single_result"]) if r["single_result"] else "-"
        for r in rows
    )

    msg = (
        f"📅 *Results ({selected_date.strftime('%d/%m/%Y')})*\n\n"
        f"{patti_row}\n"
        f"{single_row}"
    )

    await query.message.edit_text(msg, parse_mode="Markdown")


# =====================================================
# MAIN MENU / START COMMAND
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
        "🎉 *Welcome to FF Game Bot!* 🎉\nChoose an option:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =====================================================
# RULES MESSAGE
# =====================================================

async def rules(update: Update, context):
    query = update.callback_query
    await query.answer()

    msg = (
        "📘 *GAME RULES*\n\n"
        "1️⃣ **Baaji Rules**\n"
        "• Baaji opens at scheduled time.\n"
        "• Betting closes automatically.\n"
        "• Next Baaji opens ONLY after admin declares result.\n\n"
        "2️⃣ **Betting Rules**\n"
        "• Max 10 bets per Baaji.\n"
        "• Bet range: 5 to 5000.\n\n"
        "3️⃣ **Wallet Rules**\n"
        "• Minimum add: 50\n"
        "• Redeem once per day\n"
        "• Winnings auto-credited\n\n"
        "4️⃣ **Result Rules**\n"
        "• Admin enters 3-digit Patti.\n"
        "• Bot calculates Single.\n"
        "• Results broadcast to all.\n"
    )

    await query.message.edit_text(msg, parse_mode="Markdown")


# =====================================================
# CALLBACK ROUTER (CLEAN & FINAL)
# =====================================================

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data

    # MAIN MENU
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

    # PLAY
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

    # WALLET
    if data == "add_points":
        return await handle_add_points(update, context)

    if data == "redeem_points":
        return await handle_redeem_points(update, context)

    if data == "bet_history":
        return await show_bet_history(update, context)

    # RESULTS
    if data == "results_today":
        return await results_today(update, context)

    if data == "results_yesterday":
        return await results_yesterday(update, context)

    if data == "results_previous":
        return await results_previous(update, context)

    if data.startswith("result_date_"):
        return await show_results_for_date(update, context)

    # ADMIN PANEL
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


# APPROVE ADD REQUEST
if data.startswith("approve_"):
    request_id = int(data.split("_")[1])

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT user_id, amount FROM add_requests WHERE id=%s AND status='pending'", (request_id,))
    row = cur.fetchone()

    if not row:
        return await update.callback_query.message.edit_text("❌ Request not found or already processed.")

    user_id, amount = row

    # Update wallet
    cur.execute("UPDATE users SET wallet = wallet + %s WHERE user_id=%s", (amount, user_id))
    cur.execute("UPDATE add_requests SET status='approved' WHERE id=%s", (request_id,))
    conn.commit()
    conn.close()

    await update.callback_query.message.edit_text(f"✅ Approved request {request_id}.\nWallet updated!")

    await context.bot.send_message(
        chat_id=user_id,
        text=f"✅ Your add request (ID: {request_id}) has been approved.\nAmount added: {amount}",
    )
    return


# REJECT ADD REQUEST
if data.startswith("reject_"):
    request_id = int(data.split("_")[1])

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT user_id, amount FROM add_requests WHERE id=%s AND status='pending'", (request_id,))
    row = cur.fetchone()

    if not row:
        return await update.callback_query.message.edit_text("❌ Request not found or already processed.")

    user_id, amount = row

    cur.execute("UPDATE add_requests SET status='rejected' WHERE id=%s", (request_id,))
    conn.commit()
    conn.close()

    await update.callback_query.message.edit_text(f"❌ Rejected request {request_id}.")

    await context.bot.send_message(
        chat_id=user_id,
        text=f"❌ Your add request (ID: {request_id}) has been rejected.",
    )
    return

# =====================================================
# ADMIN ADD/DEDUCT POINTS COMMANDS
# =====================================================

async def admin_add_points_menu(update: Update, context):
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("Use:\n/addpoints user_id amount")


async def admin_deduct_points_menu(update: Update, context):
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("Use:\n/deductpoints user_id amount")


async def addpoints_cmd(update: Update, context):
    """
    Admin command: /addpoints user amount
    """
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Unauthorized.")

    if len(context.args) != 2:
        return await update.message.reply_text("Use: /addpoints user amount")

    user_id = int(context.args[0])
    amount = int(context.args[1])
# =============================
# APPROVE ADD REQUEST
# =============================
async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ You are not authorized.")

    if len(context.args) != 1 or not context.args[0].isdigit():
        return await update.message.reply_text("Usage: /approve request_id")

    request_id = int(context.args[0])

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT user_id, amount, status FROM add_requests WHERE id = %s", (request_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return await update.message.reply_text("❌ Invalid request ID.")

    user_id, amount, status = row

    if status != "pending":
        conn.close()
        return await update.message.reply_text("⚠ Request already processed.")

    cur.execute("UPDATE users SET wallet = wallet + %s WHERE user_id = %s", (amount, user_id))
    cur.execute("UPDATE add_requests SET status = 'approved' WHERE id = %s", (request_id,))

    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Request {request_id} approved.\nWallet updated.")

    await context.bot.send_message(
        chat_id=user_id,
        text=f"🎉 Your add request (ID: {request_id}) for {amount} points has been approved!",
        parse_mode="Markdown"
    )


# =============================
# REJECT ADD REQUEST
# =============================
async def reject_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ You are not authorized.")

    if len(context.args) != 1 or not context.args[0].isdigit():
        return await update.message.reply_text("Usage: /reject request_id")

    request_id = int(context.args[0])

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT user_id, amount, status FROM add_requests WHERE id = %s", (request_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return await update.message.reply_text("❌ Invalid request ID.")

    user_id, amount, status = row

    if status != "pending":
        conn.close()
        return await update.message.reply_text("⚠ Request already processed.")

    cur.execute("UPDATE add_requests SET status = 'rejected' WHERE id = %s", (request_id,))

    conn.commit()
    conn.close()

    await update.message.reply_text(f"❌ Request {request_id} rejected.")

    await context.bot.send_message(
        chat_id=user_id,
        text=f"⚠ Your add request (ID: {request_id}) has been rejected.",
        parse_mode="Markdown"
        )
    

async def deductpoints_cmd(update: Update, context):
    """
    Admin command: /deductpoints user amount
    """
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Unauthorized.")

    if len(context.args) != 2:
        return await update.message.reply_text("Use: /deductpoints user amount")

    user_id = int(context.args[0])
    amount = int(context.args[1])

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
        SET wallet = wallet - %s, last_redeem=%s
        WHERE user_id=%s;
    """, (amount, date.today(), user_id))

    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Deducted {amount} points from {user_id}")


# =====================================================
# ADMIN STATS
# =====================================================

async def admin_stats(update: Update, context):
    """
    Show total users, total bets, today stats.
    """
    query = update.callback_query
    await query.answer()

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM users;")
    total_users = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM bets;")
    total_bets = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM baaji WHERE date=%s;", (date.today(),))
    todays_baajis = cur.fetchone()[0]

    conn.close()

    msg = (
        "📊 *ADMIN STATS*\n\n"
        f"👥 Total Users: {total_users}\n"
        f"🎲 Total Bets: {total_bets}\n"
        f"📅 Today's Baajis Created: {todays_baajis}\n"
    )

    await query.message.edit_text(msg, parse_mode="Markdown")


# =====================================================
# BET HISTORY
# =====================================================

async def show_bet_history(update: Update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT baaji_id, type, digit, amount
        FROM bets
        WHERE user_id=%s
        ORDER BY id DESC LIMIT 20;
    """, (user_id,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return await query.message.edit_text("📜 No bet history found.")

    msg = "📜 *Your Last 20 Bets:*\n\n"
    for r in rows:
        msg += f"Baaji {r['baaji_id']} — {r['type']} {r['digit']} | {r['amount']}\n"

    await query.message.edit_text(msg, parse_mode="Markdown")


# =====================================================
# MAIN() — REGISTER HANDLERS & START BOT
# =====================================================

def main():
    init_db()  # Ensure tables exist

    app = Application.builder().token(BOT_TOKEN).build()

    # COMMAND HANDLERS
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addpoints", addpoints_cmd))
    app.add_handler(CommandHandler("deductpoints", deductpoints_cmd))

    # CALLBACK HANDLER
    app.add_handler(CallbackQueryHandler(callback_router))

    # TEXT INPUT HANDLER
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_add_amount))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_redeem_amount))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_patti_input))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_bet_amount))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_process_result))
    app.add_handler(MessageHandler(filters.PHOTO, process_screenshot))
    

    # SCHEDULED JOBS
    app.job_queue.run_repeating(auto_close_baaji, interval=30, first=10)
    app.job_queue.run_repeating(midnight_reset, interval=60, first=20)

    print("🚀 BOT IS RUNNING...")
    app.run_polling()


# =====================================================
# START THE BOT
# =====================================================

if __name__ == "__main__":
    main()

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
import os

TOKEN = os.getenv("BOT_TOKEN")

# 🔐 PUT YOUR TELEGRAM ID HERE
ADMIN_ID = 891656290   # <-- replace with your real ID

users = set()

def is_admin(update: Update):
    return update.effective_user.id == ADMIN_ID

async def start(update: Update, context):
    users.add(update.effective_user.id)
    await update.message.reply_text(
        "🤖 Bot is LIVE!\nYou can chat normally."
    )

async def echo(update: Update, context):
    await update.message.reply_text(update.message.text)

# 🔐 Admin-only command
async def admin(update: Update, context):
    if not is_admin(update):
        await update.message.reply_text("❌ You are not authorized.")
        return

    await update.message.reply_text(
        "✅ Admin Panel\n\n"
        "/users – total users\n"
        "/broadcast <msg> – send to all\n"
        "/ping – bot status"
    )

async def users_count(update: Update, context):
    if not is_admin(update):
        await update.message.reply_text("❌ You are not authorized.")
        return

    await update.message.reply_text(f"👥 Total users: {len(users)}")

async def broadcast(update: Update, context):
    if not is_admin(update):
        await update.message.reply_text("❌ You are not authorized.")
        return

    if not context.args:
        await update.message.reply_text("Usage:\n/broadcast Your message")
        return

    message = " ".join(context.args)

    sent = 0
    for user_id in users:
        try:
            await context.bot.send_message(user_id, message)
            sent += 1
        except:
            pass

    await update.message.reply_text(f"✅ Message sent to {sent} users.")

async def ping(update: Update, context):
    if not is_admin(update):
        await update.message.reply_text("❌ You are not authorized.")
        return

    await update.message.reply_text("🏓 Pong! Bot is running.")

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(CommandHandler("users", users_count))
app.add_handler(CommandHandler("broadcast", broadcast))
app.add_handler(CommandHandler("ping", ping))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

app.run_polling()

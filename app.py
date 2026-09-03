import os
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

app = Flask(__name__)

# Access control
ACCESS_CODE = os.environ.get("ACCESS_CODE", "")

# Users who successfully entered the access code
authorized_users = set()


@app.route("/")
def home():
    return "Priyanithan AI Bot is running"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id in authorized_users:
        await update.message.reply_text(
            "🔓 Access already authorized.\n\n"
            "🤖 Priyanithan AI Signal Bot\n"
            "Auto-trade: OFF\n"
            "Martingale: OFF"
        )
    else:
        await update.message.reply_text(
            "🔐 Private Access\n\n"
            "Please enter:\n"
            "/access YOUR_CODE"
        )


async def access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not ACCESS_CODE:
        await update.message.reply_text(
            "⚠️ Access system is not configured yet."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/access YOUR_CODE"
        )
        return

    supplied_code = context.args[0]

    if supplied_code == ACCESS_CODE:
        authorized_users.add(user_id)

        await update.message.reply_text(
            "✅ ACCESS GRANTED\n\n"
            "🤖 Priyanithan AI Signal Bot\n"
            "📊 Signal access: ENABLED\n"
            "⚡ Auto-trade: OFF\n"
            "🛑 Martingale: OFF"
        )
    else:
        await update.message.reply_text(
            "❌ ACCESS DENIED\n"
            "Invalid access code."
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in authorized_users:
        await update.message.reply_text(
            "🔒 Access denied.\nUse /access YOUR_CODE first."
        )
        return

    await update.message.reply_text(
        "🟢 Bot Status: ONLINE\n"
        "🔐 Access: AUTHORIZED\n"
        "📊 Signal Engine: Setup stage\n"
        "🤖 AI Analysis: Pending\n"
        "⚡ Auto-trade: OFF\n"
        "🛑 Martingale: OFF"
    )


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")

    if not token:
        print("TELEGRAM_BOT_TOKEN is not configured")
        return

    telegram_app = Application.builder().token(token).build()

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("access", access))
    telegram_app.add_handler(CommandHandler("status", status))

    threading.Thread(target=run_web, daemon=True).start()

    telegram_app.run_polling()


if __name__ == "__main__":
    main()

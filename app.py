import os
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

app = Flask(__name__)

@app.route("/")
def home():
    return "Priyanithan AI Bot is running"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Priyanithan AI Signal Bot\n\n"
        "Bot is online.\n"
        "Auto-trade: OFF\n"
        "Martingale: OFF"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🟢 Bot Status: ONLINE\n"
        "📊 Signal Engine: Setup stage\n"
        "🤖 AI Analysis: Pending\n"
        "⚡ Auto-trade: OFF"
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
    telegram_app.add_handler(CommandHandler("status", status))

    threading.Thread(target=run_web, daemon=True).start()

    telegram_app.run_polling()

if __name__ == "__main__":
    main()

import os
import base64
import threading

from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from openai import OpenAI


app = Flask(__name__)

ACCESS_CODE = os.environ.get("ACCESS_CODE", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

authorized_users = set()

openai_client = None

if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)


# =========================
# WEB
# =========================

@app.route("/")
def home():
    return "Priyanithan AI Bot is running"


# =========================
# AI SCREENSHOT ANALYSIS
# =========================

def analyze_chart_image(image_bytes):

    if not openai_client:
        return (
            "⚠️ AI is not configured.\n\n"
            "Render Environment Variable:\n"
            "OPENAI_API_KEY is missing."
        )

    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    prompt = """
You are a careful technical chart analyst.

Analyze ONLY the information visible in the supplied trading-chart screenshot.

Do NOT invent prices, indicators, candles, timeframe, asset name, or market data.

Identify when visible:
- Asset
- Timeframe
- Current price
- EMA 9
- EMA 21
- EMA 50
- EMA 200
- RSI
- MACD
- Bollinger Bands
- Stochastic
- ADX
- Support/resistance
- Candlestick pattern
- Trend
- Momentum

Then compare the visible evidence.

IMPORTANT:
- Do not claim certainty.
- Do not say 100% accurate.
- Do not treat confidence as win probability.
- If the screenshot does not provide enough evidence, return NO SIGNAL.
- Do not use external/live market data.
- Do not execute any trade.

Signal rules:
CALL only when multiple visible confirmations agree.
PUT only when multiple visible confirmations agree.
Otherwise NO SIGNAL.

Return this format:

📊 AI CHART ANALYSIS

Asset: ...
Timeframe: ...

Trend: ...
Momentum: ...

EMA: ...
RSI: ...
MACD: ...
Bollinger: ...
Stochastic: ...
ADX: ...

Support/Resistance: ...
Candlestick: ...

CALL confirmations: ...
PUT confirmations: ...

🎯 SIGNAL: CALL / PUT / NO SIGNAL

Confidence: .../100
AI Decision: APPROVE / REJECT

Reason:
...

⚡ Auto-trade: OFF
🛑 Martingale: OFF

If an indicator is not visible, write:
NOT VISIBLE
"""

    try:

        response = openai_client.responses.create(
            model="gpt-5.6-luna",
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": prompt,
                        },
                        {
                            "type": "input_image",
                            "image_url": (
                                "data:image/jpeg;base64,"
                                + image_base64
                            ),
                        },
                    ],
                }
            ],
        )

        return response.output_text

    except Exception as e:

        print("AI analysis error:", e)

        return (
            "❌ AI analysis failed.\n\n"
            "No signal generated."
        )


# =========================
# START
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if user_id in authorized_users:

        await update.message.reply_text(
            "🔓 Access authorized\n\n"
            "🤖 Priyanithan AI Signal Bot\n"
            "📸 Screenshot Analysis: READY\n"
            "📊 AI + Indicators: READY\n"
            "⚡ Auto-trade: OFF\n"
            "🛑 Martingale: OFF"
        )

    else:

        await update.message.reply_text(
            "🔐 Private Access\n\n"
            "/access YOUR_CODE"
        )


# =========================
# ACCESS
# =========================

async def access(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if not ACCESS_CODE:

        await update.message.reply_text(
            "⚠️ ACCESS_CODE is not configured."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Usage:\n/access YOUR_CODE"
        )

        return

    if context.args[0] == ACCESS_CODE:

        authorized_users.add(user_id)

        await update.message.reply_text(
            "✅ ACCESS GRANTED\n\n"
            "📸 Screenshot Analysis: READY\n"
            "📊 AI + Indicators: READY\n"
            "⚡ Auto-trade: OFF\n"
            "🛑 Martingale: OFF"
        )

    else:

        await update.message.reply_text(
            "❌ ACCESS DENIED"
        )


# =========================
# STATUS
# =========================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if user_id not in authorized_users:

        await update.message.reply_text(
            "🔒 Access denied."
        )

        return

    ai_status = (
        "READY"
        if openai_client
        else "NOT CONFIGURED"
    )

    await update.message.reply_text(

        "🟢 Bot Status: ONLINE\n"
        "🔐 Access: AUTHORIZED\n"
        "📸 Screenshot Analysis: READY\n"
        f"🤖 AI Engine: {ai_status}\n"
        "📈 EMA: READY\n"
        "📊 RSI: READY\n"
        "📉 MACD: AI SCREENSHOT\n"
        "〰️ Bollinger: AI SCREENSHOT\n"
        "📐 Stochastic: AI SCREENSHOT\n"
        "💪 ADX: AI SCREENSHOT\n"
        "⚡ Auto-trade: OFF\n"
        "🛑 Martingale: OFF"

    )


# =========================
# PHOTO HANDLER
# =========================

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if user_id not in authorized_users:

        await update.message.reply_text(
            "🔒 Access denied."
        )

        return

    await update.message.reply_text(
        "🔎 Screenshot received.\n\n"
        "🤖 AI + indicator analysis running...\n"
        "⏳ Please wait..."
    )

    try:

        photo = update.message.photo[-1]

        telegram_file = await photo.get_file()

        image_bytes = await telegram_file.download_as_bytearray()

        result = analyze_chart_image(
            bytes(image_bytes)
        )

        await update.message.reply_text(
            result
        )

    except Exception as e:

        print("Photo error:", e)

        await update.message.reply_text(
            "❌ Screenshot analysis failed.\n"
            "No signal generated."
        )


# =========================
# WEB SERVER
# =========================

def run_web():

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )


# =========================
# MAIN
# =========================

def main():

    if not TELEGRAM_BOT_TOKEN:

        print(
            "TELEGRAM_BOT_TOKEN is not configured"
        )

        return

    telegram_app = (
        Application
        .builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    telegram_app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "access",
            access
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "status",
            status
        )
    )

    telegram_app.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo
        )
    )

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    telegram_app.run_polling()


if __name__ == "__main__":
    main()

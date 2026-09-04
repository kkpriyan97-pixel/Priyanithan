import os
import threading
import asyncio
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

app = Flask(__name__)

ACCESS_CODE = os.environ.get("ACCESS_CODE", "")
authorized_users = set()


@app.route("/")
def home():
    return "Priyanithan AI Bot is running"


# =========================
# INDICATOR ENGINE
# =========================

def analyze_candles(candles):
    if len(candles) < 200:
        return {
            "signal": "NO SIGNAL",
            "reason": "Need at least 200 candles"
        }

    closes = [float(x["close"]) for x in candles]
    highs = [float(x["high"]) for x in candles]
    lows = [float(x["low"]) for x in candles]

    # Simple EMA
    def ema(values, period):
        multiplier = 2 / (period + 1)
        result = values[0]

        for price in values[1:]:
            result = (price - result) * multiplier + result

        return result

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)

    call = 0
    put = 0
    reasons = []

    # EMA trend
    if ema9 > ema21 > ema50 > ema200:
        call += 1
        reasons.append("EMA bullish")

    elif ema9 < ema21 < ema50 < ema200:
        put += 1
        reasons.append("EMA bearish")

    # RSI
    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]

        if change >= 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[-14:]) / 14
    avg_loss = sum(losses[-14:]) / 14

    if avg_loss == 0:
        rsi = 100
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

    if 50 < rsi < 70:
        call += 1
        reasons.append("RSI bullish")

    elif 30 < rsi < 50:
        put += 1
        reasons.append("RSI bearish")

    # Price momentum
    if closes[-1] > closes[-2] > closes[-3]:
        call += 1
        reasons.append("Price momentum bullish")

    elif closes[-1] < closes[-2] < closes[-3]:
        put += 1
        reasons.append("Price momentum bearish")

    # Recent candle
    last = candles[-1]

    if float(last["close"]) > float(last["open"]):
        call += 1
        reasons.append("Bullish candle")

    elif float(last["close"]) < float(last["open"]):
        put += 1
        reasons.append("Bearish candle")

    # Final decision
    if call >= 3 and call > put:
        return {
            "signal": "CALL",
            "confidence": min(99, 70 + call * 5),
            "confirmations": call,
            "rsi": round(rsi, 2),
            "reasons": reasons
        }

    if put >= 3 and put > call:
        return {
            "signal": "PUT",
            "confidence": min(99, 70 + put * 5),
            "confirmations": put,
            "rsi": round(rsi, 2),
            "reasons": reasons
        }

    return {
        "signal": "NO SIGNAL",
        "confidence": 0,
        "confirmations": max(call, put),
        "rsi": round(rsi, 2),
        "reasons": reasons
    }


# =========================
# TELEGRAM
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    if user_id in authorized_users:
        await update.message.reply_text(
            "🔓 Access authorized\n\n"
            "🤖 Priyanithan AI Signal Bot\n"
            "📊 Indicator Engine: READY\n"
            "⚡ Auto-trade: OFF\n"
            "🛑 Martingale: OFF"
        )
    else:
        await update.message.reply_text(
            "🔐 Private Access\n\n"
            "/access YOUR_CODE"
        )


async def access(update: Update, context: ContextTypes.DEFAULT_TYPE):

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
            "📊 Signal Engine: READY\n"
            "⚡ Auto-trade: OFF\n"
            "🛑 Martingale: OFF"
        )

    else:

        await update.message.reply_text(
            "❌ ACCESS DENIED"
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    if user_id not in authorized_users:
        await update.message.reply_text(
            "🔒 Access denied."
        )
        return

    await update.message.reply_text(
        "🟢 Bot Status: ONLINE\n"
        "🔐 Access: AUTHORIZED\n"
        "📊 Indicator Engine: READY\n"
        "📈 EMA: READY\n"
        "📊 RSI: READY\n"
        "⚡ Auto-trade: OFF\n"
        "🛑 Martingale: OFF\n\n"
        "📡 Market Data: NOT CONNECTED YET"
    )


def run_web():

    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )


def main():

    token = os.environ.get("TELEGRAM_BOT_TOKEN")

    if not token:
        print("TELEGRAM_BOT_TOKEN is not configured")
        return

    telegram_app = (
        Application
        .builder()
        .token(token)
        .build()
    )

    telegram_app.add_handler(
        CommandHandler("start", start)
    )

    telegram_app.add_handler(
        CommandHandler("access", access)
    )

    telegram_app.add_handler(
        CommandHandler("status", status)
    )

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    telegram_app.run_polling()


if __name__ == "__main__":
    main()

import os
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import ta_py as ta

app = Flask(__name__)

# =========================
# ACCESS CONTROL
# =========================

ACCESS_CODE = os.environ.get("ACCESS_CODE", "")
authorized_users = set()


# =========================
# WEB
# =========================

@app.route("/")
def home():
    return "Priyanithan AI Bot is running"


# =========================
# INDICATOR ENGINE
# =========================

def analyze_indicators(candles):
    """
    candles format:
    [
        {"open": 1, "high": 2, "low": 0.5, "close": 1.5},
        ...
    ]

    Minimum recommended candles: 200+
    """

    if not candles or len(candles) < 200:
        return {
            "signal": "NO SIGNAL",
            "confidence": 0,
            "reason": "Not enough candle data"
        }

    closes = [float(x["close"]) for x in candles]
    highs = [float(x["high"]) for x in candles]
    lows = [float(x["low"]) for x in candles]

    score_call = 0
    score_put = 0
    confirmations = []

    # -------------------------
    # EMA
    # -------------------------

    ema9 = ta.ema(closes, 9)[-1]
    ema21 = ta.ema(closes, 21)[-1]
    ema50 = ta.ema(closes, 50)[-1]
    ema200 = ta.ema(closes, 200)[-1]

    price = closes[-1]

    if price > ema9 > ema21 > ema50 > ema200:
        score_call += 1
        confirmations.append("EMA bullish")

    elif price < ema9 < ema21 < ema50 < ema200:
        score_put += 1
        confirmations.append("EMA bearish")

    # -------------------------
    # RSI
    # -------------------------

    rsi = ta.rsi(closes, 14)[-1]

    if 50 < rsi < 70:
        score_call += 1
        confirmations.append("RSI bullish")

    elif 30 < rsi < 50:
        score_put += 1
        confirmations.append("RSI bearish")

    # -------------------------
    # MACD
    # -------------------------

    macd_data = ta.macd(closes, 12, 26)

    if macd_data:
        macd_value = macd_data[-1]

        if isinstance(macd_value, (list, tuple)):
            macd_line = macd_value[0]
            signal_line = macd_value[1]

            if macd_line > signal_line:
                score_call += 1
                confirmations.append("MACD bullish")

            elif macd_line < signal_line:
                score_put += 1
                confirmations.append("MACD bearish")

    # -------------------------
    # Bollinger Bands
    # -------------------------

    bands = ta.bands(closes, length=20, deviations=2)

    if bands:
        last_band = bands[-1]

        if isinstance(last_band, (list, tuple)) and len(last_band) >= 3:
            lower = last_band[0]
            middle = last_band[1]
            upper = last_band[2]

            if price > middle:
                score_call += 1
                confirmations.append("Bollinger bullish")

            elif price < middle:
                score_put += 1
                confirmations.append("Bollinger bearish")

    # -------------------------
    # Stochastic
    # -------------------------

    stoch = ta.stoch(
        [
            [h, l, c]
            for h, l, c in zip(highs, lows, closes)
        ],
        14,
        3
    )

    if stoch:
        last_stoch = stoch[-1]

        if isinstance(last_stoch, (list, tuple)):
            k = last_stoch[0]

            if k > 50:
                score_call += 1
                confirmations.append("Stochastic bullish")

            elif k < 50:
                score_put += 1
                confirmations.append("Stochastic bearish")

    # -------------------------
    # ADX
    # -------------------------

    adx_data = ta.adx(
        [
            [h, l, c]
            for h, l, c in zip(highs, lows, closes)
        ],
        14
    )

    trend_strength = 0

    if adx_data:
        last_adx = adx_data[-1]

        if isinstance(last_adx, (list, tuple)):
            trend_strength = last_adx[0]

    # -------------------------
    # FINAL DECISION
    # -------------------------

    total_confirmations = max(score_call, score_put)

    if total_confirmations < 3:
        return {
            "signal": "NO SIGNAL",
            "confidence": 0,
            "call_score": score_call,
            "put_score": score_put,
            "adx": trend_strength,
            "confirmations": confirmations
        }

    if score_call > score_put:
        confidence = min(99, 60 + score_call * 7)

        return {
            "signal": "CALL",
            "confidence": confidence,
            "call_score": score_call,
            "put_score": score_put,
            "adx": trend_strength,
            "confirmations": confirmations
        }

    if score_put > score_call:
        confidence = min(99, 60 + score_put * 7)

        return {
            "signal": "PUT",
            "confidence": confidence,
            "call_score": score_call,
            "put_score": score_put,
            "adx": trend_strength,
            "confirmations": confirmations
        }

    return {
        "signal": "NO SIGNAL",
        "confidence": 0,
        "call_score": score_call,
        "put_score": score_put,
        "adx": trend_strength,
        "confirmations": confirmations
    }


# =========================
# TELEGRAM /start
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    if user_id in authorized_users:

        await update.message.reply_text(
            "🔓 Access already authorized.\n\n"
            "🤖 Priyanithan AI Signal Bot\n"
            "📊 Indicator Engine: READY\n"
            "⚡ Auto-trade: OFF\n"
            "🛑 Martingale: OFF"
        )

    else:

        await update.message.reply_text(
            "🔐 Private Access\n\n"
            "Please enter:\n"
            "/access YOUR_CODE"
        )


# =========================
# /access
# =========================

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
            "📊 Indicator Engine: READY\n"
            "⚡ Auto-trade: OFF\n"
            "🛑 Martingale: OFF"
        )

    else:

        await update.message.reply_text(
            "❌ ACCESS DENIED\n"
            "Invalid access code."
        )


# =========================
# /status
# =========================

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    if user_id not in authorized_users:

        await update.message.reply_text(
            "🔒 Access denied.\n"
            "Use /access YOUR_CODE first."
        )
        return

    await update.message.reply_text(
        "🟢 Bot Status: ONLINE\n"
        "🔐 Access: AUTHORIZED\n"
        "📊 Indicator Engine: READY\n"
        "📈 EMA: READY\n"
        "📊 RSI: READY\n"
        "📉 MACD: READY\n"
        "〰️ Bollinger: READY\n"
        "📐 Stochastic: READY\n"
        "💪 ADX: READY\n"
        "⚡ Auto-trade: OFF\n"
        "🛑 Martingale: OFF"
    )


# =========================
# WEB SERVER
# =========================

def run_web():

    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )


# =========================
# MAIN
# =========================

def main():

    token = os.environ.get("TELEGRAM_BOT_TOKEN")

    if not token:

        print(
            "TELEGRAM_BOT_TOKEN is not configured"
        )

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

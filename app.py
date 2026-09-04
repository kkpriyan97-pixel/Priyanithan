import os
import threading
import time
import requests

from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


app = Flask(__name__)

ACCESS_CODE = os.environ.get("ACCESS_CODE", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
LIVE_RATES_API_KEY = os.environ.get("LIVE_RATES_API_KEY", "")

authorized_users = set()


# =========================
# WEB
# =========================

@app.route("/")
def home():
    return "Priyanithan AI Bot is running"


# =========================
# MARKET DATA
# =========================

def get_market_price(pair):

    url = "https://www.live-rates.com/api/price"

    params = {
        "rate": pair
    }

    if LIVE_RATES_API_KEY:
        params["key"] = LIVE_RATES_API_KEY

    try:
        response = requests.get(
            url,
            params=params,
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        if not data:
            return None

        item = data[0]

        return {
            "pair": pair,
            "bid": float(item["bid"]),
            "ask": float(item["ask"]),
            "high": float(item["high"]),
            "low": float(item["low"]),
            "open": float(item["open"]),
            "timestamp": item["timestamp"]
        }

    except Exception as e:

        print("Market data error:", e)

        return None


def market_data_test():

    eurusd = get_market_price("EURUSD")
    gbpusd = get_market_price("GBPUSD")

    return {
        "EUR/USD": eurusd,
        "GBP/USD": gbpusd
    }


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

    # EMA
    def ema(values, period):

        multiplier = 2 / (period + 1)

        result = values[0]

        for price in values[1:]:
            result = (
                (price - result) * multiplier
                + result
            )

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

        rsi = 100 - (
            100 / (1 + rs)
        )


    if 50 < rsi < 70:

        call += 1
        reasons.append("RSI bullish")

    elif 30 < rsi < 50:

        put += 1
        reasons.append("RSI bearish")


    # Momentum
    if closes[-1] > closes[-2] > closes[-3]:

        call += 1
        reasons.append("Momentum bullish")

    elif closes[-1] < closes[-2] < closes[-3]:

        put += 1
        reasons.append("Momentum bearish")


    # Final signal
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
# TELEGRAM /START
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
            "📊 Indicator Engine: READY\n"
            "📡 Market Data: TESTING\n"
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
            "📊 Signal Engine: READY\n"
            "📡 Market Data: TESTING\n"
            "⚡ Auto-trade: OFF\n"
            "🛑 Martingale: OFF"
        )

    else:

        await update.message.reply_text(
            "❌ ACCESS DENIED"
        )


# =========================
# MARKET TEST COMMAND
# =========================

async def market(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if user_id not in authorized_users:

        await update.message.reply_text(
            "🔒 Access denied."
        )

        return


    data = market_data_test()

    message = "📡 MARKET DATA TEST\n\n"

    for pair, value in data.items():

        if value:

            message += (
                f"🟢 {pair}\n"
                f"Bid: {value['bid']}\n"
                f"Ask: {value['ask']}\n"
                f"High: {value['high']}\n"
                f"Low: {value['low']}\n\n"
            )

        else:

            message += (
                f"🔴 {pair}\n"
                f"Data unavailable\n\n"
            )


    await update.message.reply_text(message)


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


    data = market_data_test()

    eur = "CONNECTED" if data["EUR/USD"] else "FAILED"
    gbp = "CONNECTED" if data["GBP/USD"] else "FAILED"


    await update.message.reply_text(

        "🟢 Bot Status: ONLINE\n"
        "🔐 Access: AUTHORIZED\n"
        "📊 Indicator Engine: READY\n"
        "📈 EMA: READY\n"
        "📊 RSI: READY\n"
        f"💱 EUR/USD: {eur}\n"
        f"💷 GBP/USD: {gbp}\n"
        "⚡ Auto-trade: OFF\n"
        "🛑 Martingale: OFF"

    )


# =========================
# FLASK
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
            "market",
            market
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "status",
            status
        )
    )


    threading.Thread(
        target=run_web,
        daemon=True
    ).start()


    telegram_app.run_polling()


if __name__ == "__main__":

    main()

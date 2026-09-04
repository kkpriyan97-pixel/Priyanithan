import os
import threading
import requests
import pandas as pd
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands


# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
AIRFORCE_API_KEY = os.getenv("AIRFORCE_API_KEY")
AIRFORCE_MODEL = os.getenv("AIRFORCE_MODEL", "gpt-4.1-mini")

AIRFORCE_URL = "https://api.airforce/v1/chat/completions"

AUTHORIZED_USERS = set()

AUTO_TRADE = False
MARTINGALE = False

app = Flask(__name__)


# =========================
# FLASK
# =========================

@app.route("/")
def home():
    return "Priyanithan AI Bot is ONLINE"


@app.route("/status")
def status():
    return (
        "🟢 Bot Status: ONLINE\n"
        "🔐 Access: AUTHORIZED\n"
        "📡 Market API: Yahoo Finance\n"
        "📊 Indicator Engine: READY\n"
        "📈 EMA 9/21/50/200: READY\n"
        "📊 RSI 14: READY\n"
        "📉 MACD: READY\n"
        "〰️ Bollinger Bands: READY\n"
        "📐 Stochastic: READY\n"
        "💪 ADX: READY\n"
        "🤖 AI Validator: READY\n"
        f"🧠 AI Model: {AIRFORCE_MODEL}\n"
        "⚡ Auto-trade: OFF\n"
        "🛑 Martingale: OFF"
    )


def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))


# =========================
# AUTH
# =========================

def is_authorized(user_id):
    return user_id in AUTHORIZED_USERS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not AUTHORIZED_USERS:
        AUTHORIZED_USERS.add(user_id)

    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text("❌ Access denied.")
        return

    await update.message.reply_text(
        "🤖 Priyanithan AI Bot\n\n"
        "Commands:\n"
        "/status\n"
        "/signal EURUSD\n"
        "/signal GBPUSD\n"
        "/aitest"
    )


# =========================
# YAHOO FINANCE DATA
# =========================

PAIR_MAP = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
}


def get_market_data(pair):
    pair = pair.upper()

    if pair not in PAIR_MAP:
        return None, "Unsupported pair"

    symbol = PAIR_MAP[pair]

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    params = {
        "interval": "1m",
        "range": "1d",
        "includePrePost": "false",
        "events": "div,splits",
    }

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        result = data["chart"]["result"][0]

        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]

        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": quote["open"],
            "high": quote["high"],
            "low": quote["low"],
            "close": quote["close"],
            "volume": quote.get(
                "volume",
                [0] * len(timestamps)
            ),
        })

        df = df.dropna()

        if len(df) < 220:
            return None, "Not enough market candles"

        return df, None

    except Exception as e:
        return None, str(e)


# =========================
# INDICATORS
# =========================

def calculate_indicators(df):

    close = df["close"]
    high = df["high"]
    low = df["low"]

    df["ema9"] = EMAIndicator(
        close=close,
        window=9
    ).ema_indicator()

    df["ema21"] = EMAIndicator(
        close=close,
        window=21
    ).ema_indicator()

    df["ema50"] = EMAIndicator(
        close=close,
        window=50
    ).ema_indicator()

    df["ema200"] = EMAIndicator(
        close=close,
        window=200
    ).ema_indicator()

    df["rsi"] = RSIIndicator(
        close=close,
        window=14
    ).rsi()

    macd = MACD(
        close=close,
        window_slow=26,
        window_fast=12,
        window_sign=9
    )

    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()

    bb = BollingerBands(
        close=close,
        window=20,
        window_dev=2
    )

    df["bb_upper"] = bb.bollinger_hband()
    df["bb_middle"] = bb.bollinger_mavg()
    df["bb_lower"] = bb.bollinger_lband()

    stoch = StochasticOscillator(
        high=high,
        low=low,
        close=close,
        window=14,
        smooth_window=3
    )

    df["stoch_k"] = stoch.stoch()
    df["stoch_d"] = stoch.stoch_signal()

    adx = ADXIndicator(
        high=high,
        low=low,
        close=close,
        window=14
    )

    df["adx"] = adx.adx()
    df["plus_di"] = adx.adx_pos()
    df["minus_di"] = adx.adx_neg()

    return df


# =========================
# INDICATOR SIGNAL
# =========================

def analyze_signal(df):

    # Use the previous candle because it is closed
    row = df.iloc[-2]

    call_reasons = []
    put_reasons = []

    # EMA
    if (
        row["ema9"] > row["ema21"]
        and row["ema21"] > row["ema50"]
        and row["ema50"] > row["ema200"]
    ):
        call_reasons.append("EMA bullish trend")

    elif (
        row["ema9"] < row["ema21"]
        and row["ema21"] < row["ema50"]
        and row["ema50"] < row["ema200"]
    ):
        put_reasons.append("EMA bearish trend")

    # RSI
    if row["rsi"] > 55:
        call_reasons.append("RSI bullish")

    elif row["rsi"] < 45:
        put_reasons.append("RSI bearish")

    # MACD
    if (
        row["macd"] > row["macd_signal"]
        and row["macd_hist"] > 0
    ):
        call_reasons.append("MACD bullish")

    elif (
        row["macd"] < row["macd_signal"]
        and row["macd_hist"] < 0
    ):
        put_reasons.append("MACD bearish")

    # Bollinger
    if row["close"] > row["bb_middle"]:
        call_reasons.append("Bollinger bullish")

    elif row["close"] < row["bb_middle"]:
        put_reasons.append("Bollinger bearish")

    # Stochastic
    if row["stoch_k"] > row["stoch_d"]:
        call_reasons.append("Stochastic bullish")

    elif row["stoch_k"] < row["stoch_d"]:
        put_reasons.append("Stochastic bearish")

    # ADX direction
    if row["adx"] >= 20:

        if row["plus_di"] > row["minus_di"]:
            call_reasons.append("ADX bullish")

        elif row["minus_di"] > row["plus_di"]:
            put_reasons.append("ADX bearish")

    call_count = len(call_reasons)
    put_count = len(put_reasons)

    # Weak trend = NO SIGNAL
    if row["adx"] < 20:
        direction = "NO SIGNAL"
    elif call_count >= 4 and call_count > put_count:
        direction = "CALL"
    elif put_count >= 4 and put_count > call_count:
        direction = "PUT"
    else:
        direction = "NO SIGNAL"

    score = max(call_count, put_count) / 6 * 100

    return {
        "price": row["close"],
        "timestamp": row["timestamp"],
        "ema9": row["ema9"],
        "ema21": row["ema21"],
        "ema50": row["ema50"],
        "ema200": row["ema200"],
        "rsi": row["rsi"],
        "macd": row["macd"],
        "macd_signal": row["macd_signal"],
        "macd_hist": row["macd_hist"],
        "bb_upper": row["bb_upper"],
        "bb_middle": row["bb_middle"],
        "bb_lower": row["bb_lower"],
        "stoch_k": row["stoch_k"],
        "stoch_d": row["stoch_d"],
        "adx": row["adx"],
        "plus_di": row["plus_di"],
        "minus_di": row["minus_di"],
        "call_count": call_count,
        "put_count": put_count,
        "call_reasons": call_reasons,
        "put_reasons": put_reasons,
        "direction": direction,
        "score": score,
    }


# =========================
# AIRFORCE AI VALIDATION
# =========================

def ai_validate(analysis):

    if not AIRFORCE_API_KEY:
        return {
            "success": False,
            "reason": "AIRFORCE_API_KEY not configured"
        }

    prompt = f"""
You are a strict trading signal validator.

Analyze the following CLOSED 1-minute candle indicator data.

Market price: {analysis['price']}

EMA9: {analysis['ema9']}
EMA21: {analysis['ema21']}
EMA50: {analysis['ema50']}
EMA200: {analysis['ema200']}

RSI: {analysis['rsi']}

MACD: {analysis['macd']}
MACD Signal: {analysis['macd_signal']}
MACD Histogram: {analysis['macd_hist']}

Bollinger Upper: {analysis['bb_upper']}
Bollinger Middle: {analysis['bb_middle']}
Bollinger Lower: {analysis['bb_lower']}

Stochastic K: {analysis['stoch_k']}
Stochastic D: {analysis['stoch_d']}

ADX: {analysis['adx']}
+DI: {analysis['plus_di']}
-DI: {analysis['minus_di']}

CALL confirmations: {analysis['call_count']}
PUT confirmations: {analysis['put_count']}

Indicator direction: {analysis['direction']}
Indicator score: {analysis['score']}

Rules:
1. Be conservative.
2. Do not invent market data.
3. APPROVE only when the indicator direction is sufficiently strong.
4. If uncertain, REJECT.
5. AI confidence must be at least 89 to approve.
6. Direction must exactly match the indicator direction.
7. This is validation only, not a guarantee of profit.
8. Return JSON only.

Required JSON:
{{
  "decision": "APPROVE" or "REJECT",
  "direction": "CALL" or "PUT" or "NO SIGNAL",
  "confidence": number,
  "reason": "short explanation"
}}
"""

    payload = {
        "model": AIRFORCE_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are a strict financial signal validator. Return JSON only."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0,
        "max_tokens": 300
    }

    headers = {
        "Authorization": f"Bearer {AIRFORCE_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            AIRFORCE_URL,
            headers=headers,
            json=payload,
            timeout=30
        )

        if response.status_code == 429:
            retry_after = response.headers.get(
                "Retry-After",
                "unknown"
            )

            return {
                "success": False,
                "reason": f"Airforce rate limit 429. Retry-After: {retry_after}"
            }

        response.raise_for_status()

        data = response.json()

        content = data["choices"][0]["message"]["content"].strip()

        # Remove accidental markdown fences
        content = content.replace("```json", "")
        content = content.replace("```", "")
        content = content.strip()

        import json

        result = json.loads(content)

        return {
            "success": True,
            "decision": str(result.get("decision", "REJECT")).upper(),
            "direction": str(result.get("direction", "NO SIGNAL")).upper(),
            "confidence": float(result.get("confidence", 0)),
            "reason": result.get("reason", "")
        }

    except Exception as e:
        return {
            "success": False,
            "reason": str(e)
        }


# =========================
# /SIGNAL
# =========================

async def signal_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if not AUTHORIZED_USERS:
        AUTHORIZED_USERS.add(user_id)

    if not is_authorized(user_id):
        await update.message.reply_text("❌ Access denied.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/signal EURUSD\n/signal GBPUSD"
        )
        return

    pair = context.args[0].upper()

    await update.message.reply_text(
        f"🔎 Analyzing {pair}..."
    )

    df, error = get_market_data(pair)

    if df is None:
        await update.message.reply_text(
            f"❌ MARKET DATA FAILED\n{error}"
        )
        return

    try:
        df = calculate_indicators(df)

        analysis = analyze_signal(df)

    except Exception as e:
        await update.message.reply_text(
            f"❌ INDICATOR ERROR\n{e}"
        )
        return

    # No indicator signal
    if analysis["direction"] == "NO SIGNAL":

        text = (
            "📊 INDICATOR ANALYSIS\n\n"
            f"💱 Market: {pair}\n"
            f"💰 Price: {analysis['price']:.5f}\n"
            "⏱ Timeframe: 1 minute\n\n"

            f"EMA9: {analysis['ema9']:.5f}\n"
            f"EMA21: {analysis['ema21']:.5f}\n"
            f"EMA50: {analysis['ema50']:.5f}\n"
            f"EMA200: {analysis['ema200']:.5f}\n\n"

            f"RSI: {analysis['rsi']:.2f}\n"
            f"MACD: {analysis['macd']:.5f}\n"
            f"Signal: {analysis['macd_signal']:.5f}\n"
            f"Histogram: {analysis['macd_hist']:.5f}\n\n"

            f"BB Upper: {analysis['bb_upper']:.5f}\n"
            f"BB Middle: {analysis['bb_middle']:.5f}\n"
            f"BB Lower: {analysis['bb_lower']:.5f}\n\n"

            f"Stoch K: {analysis['stoch_k']:.2f}\n"
            f"Stoch D: {analysis['stoch_d']:.2f}\n\n"

            f"ADX: {analysis['adx']:.2f}\n"
            f"+DI: {analysis['plus_di']:.2f}\n"
            f"-DI: {analysis['minus_di']:.2f}\n\n"

            f"🟢 CALL confirmations: {analysis['call_count']}\n"
            f"🔴 PUT confirmations: {analysis['put_count']}\n\n"

            "🎯 SIGNAL: NO SIGNAL\n"
            f"📈 Indicator score: {analysis['score']:.1f}%\n\n"

            "⚠️ ADX/trend strength is insufficient.\n"
            "⚠️ Data source: Yahoo Finance\n"
            "⚠️ This is not an Olymp Trade price feed."
        )

        await update.message.reply_text(text)
        return

    # =========================
    # AI VALIDATION
    # =========================

    ai = ai_validate(analysis)

    if not ai["success"]:

        await update.message.reply_text(
            "🤖 AI VALIDATION FAILED\n\n"
            f"Reason: {ai['reason']}\n\n"
            "🎯 FINAL SIGNAL: NO SIGNAL"
        )

        return

    # AI must approve
    if (
        ai["decision"] != "APPROVE"
        or ai["direction"] != analysis["direction"]
        or ai["confidence"] < 89
    ):

        await update.message.reply_text(
            "🤖 AI VALIDATION\n\n"
            f"Decision: {ai['decision']}\n"
            f"Direction: {ai['direction']}\n"
            f"AI Confidence: {ai['confidence']:.1f}%\n"
            f"Reason: {ai['reason']}\n\n"
            "🎯 FINAL SIGNAL: NO SIGNAL"
        )

        return

    # =========================
    # FINAL SIGNAL
    # =========================

    direction_emoji = (
        "🟢 CALL"
        if analysis["direction"] == "CALL"
        else "🔴 PUT"
    )

    call_reason_text = "\n".join(
        f"• {x}" for x in analysis["call_reasons"]
    )

    put_reason_text = "\n".join(
        f"• {x}" for x in analysis["put_reasons"]
    )

    text = (
        "🚨 AI VALIDATED SIGNAL 🚨\n\n"

        f"💱 Market: {pair}\n"
        f"💰 Price: {analysis['price']:.5f}\n"
        "⏱ Timeframe: 1 minute\n\n"

        f"🎯 SIGNAL: {direction_emoji}\n\n"

        f"📊 Indicator score: {analysis['score']:.1f}%\n"
        f"🤖 AI confidence: {ai['confidence']:.1f}%\n"
        f"🧠 AI decision: {ai['decision']}\n\n"

        f"🟢 CALL confirmations: {analysis['call_count']}\n"
        f"{call_reason_text}\n\n"

        f"🔴 PUT confirmations: {analysis['put_count']}\n"
        f"{put_reason_text}\n\n"

        f"🤖 AI reason:\n{ai['reason']}\n\n"

        "⚠️ Data source: Yahoo Finance\n"
        "⚠️ This is not an Olymp Trade price feed.\n"
        "⚠️ AI confidence is not a guaranteed win probability.\n"
        "⚡ Auto-trade: OFF\n"
        "🛑 Martingale: OFF\n"
        "✋ Manual execution only."
    )

    await update.message.reply_text(text)


# =========================
# /AITEST
# =========================

async def ai_test(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if not AUTHORIZED_USERS:
        AUTHORIZED_USERS.add(user_id)

    if not is_authorized(user_id):
        await update.message.reply_text("❌ Access denied.")
        return

    if not AIRFORCE_API_KEY:
        await update.message.reply_text(
            "❌ AIRFORCE_API_KEY is not configured in Render."
        )
        return

    payload = {
        "model": AIRFORCE_MODEL,
        "messages": [
            {
                "role": "user",
                "content": (
                    'Reply with exactly this JSON: '
                    '{"status":"OK","message":"AI CONNECTED"}'
                )
            }
        ],
        "temperature": 0,
        "max_tokens": 100
    }

    headers = {
        "Authorization": f"Bearer {AIRFORCE_API_KEY}",
        "Content-Type": "application/json"
    }

    try:

        response = requests.post(
            AIRFORCE_URL,
            headers=headers,
            json=payload,
            timeout=30
        )

        if response.status_code == 429:

            retry_after = response.headers.get(
                "Retry-After",
                "unknown"
            )

            await update.message.reply_text(
                "❌ AIRFORCE RATE LIMIT\n\n"
                f"HTTP 429\n"
                f"Retry-After: {retry_after}\n\n"
                "Please wait before testing again."
            )

            return

        response.raise_for_status()

        data = response.json()

        content = data["choices"][0]["message"]["content"]

        await update.message.reply_text(
            "✅ AI CONNECTION SUCCESS\n\n"
            f"Model: {AIRFORCE_MODEL}\n\n"
            f"Response:\n{content}"
        )

    except Exception as e:

        await update.message.reply_text(
            "❌ AI CONNECTION FAILED\n\n"
            f"Reason: {e}"
        )


# =========================
# BOT START
# =========================

def main():

    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN missing")
        return

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    telegram_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    telegram_app.add_handler(
        CommandHandler("start", start)
    )

    telegram_app.add_handler(
        CommandHandler("status", status_command)
    )

    telegram_app.add_handler(
        CommandHandler("signal", signal_command)
    )

    telegram_app.add_handler(
        CommandHandler("aitest", ai_test)
    )

    print("🤖 Priyanithan AI Bot started")

    telegram_app.run_polling(
        drop_pending_updates=True
    )


# =========================
# STATUS COMMAND
# =========================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if not AUTHORIZED_USERS:
        AUTHORIZED_USERS.add(user_id)

    if not is_authorized(user_id):
        await update.message.reply_text("❌ Access denied.")
        return

    await update.message.reply_text(
        "🟢 Bot Status: ONLINE\n"
        "🔐 Access: AUTHORIZED\n"
        "📡 Market API: Yahoo Finance\n"
        "📊 Indicator Engine: READY\n"
        "📈 EMA 9/21/50/200: READY\n"
        "📊 RSI 14: READY\n"
        "📉 MACD: READY\n"
        "〰️ Bollinger Bands: READY\n"
        "📐 Stochastic: READY\n"
        "💪 ADX: READY\n"
        "🤖 AI Validator: READY\n"
        f"🧠 AI Model: {AIRFORCE_MODEL}\n"
        "⚡ Auto-trade: OFF\n"
        "🛑 Martingale: OFF"
    )


if __name__ == "__main__":
    main()

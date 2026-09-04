import os
import time
import threading
import requests
import pandas as pd

from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands


app = Flask(__name__)

# =========================================================
# ENVIRONMENT
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ACCESS_CODE = os.getenv("ACCESS_CODE", "")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "")

authorized_users = set()


# =========================================================
# WEB
# =========================================================

@app.route("/")
def home():
    return "Priyanithan Indicator Signal Bot is running"


# =========================================================
# MARKET DATA
# =========================================================

def get_fx_data(from_symbol="EUR", to_symbol="USD", interval="1min"):
    if not ALPHA_VANTAGE_API_KEY:
        raise RuntimeError("ALPHA_VANTAGE_API_KEY is missing")

    url = "https://www.alphavantage.co/query"

    params = {
        "function": "FX_INTRADAY",
        "from_symbol": from_symbol,
        "to_symbol": to_symbol,
        "interval": interval,
        "outputsize": "full",
        "apikey": ALPHA_VANTAGE_API_KEY,
    }

    response = requests.get(
        url,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if "Error Message" in data:
        raise RuntimeError(data["Error Message"])

    if "Note" in data:
        raise RuntimeError(data["Note"])

    if "Information" in data:
        raise RuntimeError(data["Information"])

    series_key = next(
        (
            key for key in data.keys()
            if key.startswith("Time Series FX")
        ),
        None
    )

    if not series_key:
        raise RuntimeError(
            "No FX intraday data returned by provider"
        )

    rows = []

    for timestamp, values in data[series_key].items():
        rows.append({
            "time": timestamp,
            "open": float(values["1. open"]),
            "high": float(values["2. high"]),
            "low": float(values["3. low"]),
            "close": float(values["4. close"]),
        })

    df = pd.DataFrame(rows)

    if df.empty:
        raise RuntimeError("Market data is empty")

    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)

    return df


# =========================================================
# INDICATOR ANALYSIS
# =========================================================

def analyze_indicators(df):

    if len(df) < 200:
        return {
            "signal": "NO SIGNAL",
            "reason": f"Need 200 candles, received {len(df)}"
        }

    close = df["close"]
    high = df["high"]
    low = df["low"]

    # -----------------------------------------------------
    # EMA
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

    df["rsi"] = RSIIndicator(
        close=close,
        window=14
    ).rsi()

    # -----------------------------------------------------
    # MACD
    # -----------------------------------------------------

    macd = MACD(
        close=close,
        window_fast=12,
        window_slow=26,
        window_sign=9
    )

    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()

    # -----------------------------------------------------
    # BOLLINGER
    # -----------------------------------------------------

    bb = BollingerBands(
        close=close,
        window=20,
        window_dev=2
    )

    df["bb_upper"] = bb.bollinger_hband()
    df["bb_middle"] = bb.bollinger_mavg()
    df["bb_lower"] = bb.bollinger_lband()

    # -----------------------------------------------------
    # STOCHASTIC
    # -----------------------------------------------------

    stoch = StochasticOscillator(
        high=high,
        low=low,
        close=close,
        window=14,
        smooth_window=3
    )

    df["stoch_k"] = stoch.stoch()
    df["stoch_d"] = stoch.stoch_signal()

    # -----------------------------------------------------
    # ADX
    # -----------------------------------------------------

    adx = ADXIndicator(
        high=high,
        low=low,
        close=close,
        window=14
    )

    df["adx"] = adx.adx()
    df["plus_di"] = adx.adx_pos()
    df["minus_di"] = adx.adx_neg()

    # -----------------------------------------------------
    # LAST CANDLE
    # -----------------------------------------------------

    last = df.iloc[-1]

    call = 0
    put = 0

    call_reasons = []
    put_reasons = []

    # -----------------------------------------------------
    # EMA TREND
    # -----------------------------------------------------

    if (
        last.ema9 >
        last.ema21 >
        last.ema50 >
        last.ema200
    ):
        call += 1
        call_reasons.append("EMA bullish trend")

    elif (
        last.ema9 <
        last.ema21 <
        last.ema50 <
        last.ema200
    ):
        put += 1
        put_reasons.append("EMA bearish trend")

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

    if 50 < last.rsi < 70:
        call += 1
        call_reasons.append(
            f"RSI bullish ({last.rsi:.1f})"
        )

    elif 30 < last.rsi < 50:
        put += 1
        put_reasons.append(
            f"RSI bearish ({last.rsi:.1f})"
        )

    # -----------------------------------------------------
    # MACD
    # -----------------------------------------------------

    if (
        last.macd > last.macd_signal
        and last.macd_hist > 0
    ):
        call += 1
        call_reasons.append("MACD bullish")

    elif (
        last.macd < last.macd_signal
        and last.macd_hist < 0
    ):
        put += 1
        put_reasons.append("MACD bearish")

    # -----------------------------------------------------
    # BOLLINGER
    # -----------------------------------------------------

    if last.close > last.bb_middle:
        call += 1
        call_reasons.append("Bollinger bullish")

    elif last.close < last.bb_middle:
        put += 1
        put_reasons.append("Bollinger bearish")

    # -----------------------------------------------------
    # STOCHASTIC
    # -----------------------------------------------------

    if (
        last.stoch_k > last.stoch_d
        and last.stoch_k < 80
    ):
        call += 1
        call_reasons.append("Stochastic bullish")

    elif (
        last.stoch_k < last.stoch_d
        and last.stoch_k > 20
    ):
        put += 1
        put_reasons.append("Stochastic bearish")

    # -----------------------------------------------------
    # ADX + DI
    # -----------------------------------------------------

    if last.adx >= 20:

        if last.plus_di > last.minus_di:
            call += 1
            call_reasons.append(
                f"ADX bullish ({last.adx:.1f})"
            )

        elif last.minus_di > last.plus_di:
            put += 1
            put_reasons.append(
                f"ADX bearish ({last.adx:.1f})"
            )

    # -----------------------------------------------------
    # FINAL SIGNAL
    # -----------------------------------------------------

    total = 6

    if call >= 4 and call > put:
        signal = "CALL"
        confirmations = call
        confidence = round(
            (call / total) * 100
        )

    elif put >= 4 and put > call:
        signal = "PUT"
        confirmations = put
        confidence = round(
            (put / total) * 100
        )

    else:
        signal = "NO SIGNAL"
        confirmations = max(call, put)
        confidence = 0

    return {
        "signal": signal,
        "confirmations": confirmations,
        "confidence": confidence,

        "price": float(last.close),

        "ema9": float(last.ema9),
        "ema21": float(last.ema21),
        "ema50": float(last.ema50),
        "ema200": float(last.ema200),

        "rsi": float(last.rsi),

        "macd": float(last.macd),
        "macd_signal": float(last.macd_signal),
        "macd_hist": float(last.macd_hist),

        "bb_upper": float(last.bb_upper),
        "bb_middle": float(last.bb_middle),
        "bb_lower": float(last.bb_lower),

        "stoch_k": float(last.stoch_k),
        "stoch_d": float(last.stoch_d),

        "adx": float(last.adx),
        "plus_di": float(last.plus_di),
        "minus_di": float(last.minus_di),

        "call_reasons": call_reasons,
        "put_reasons": put_reasons,

        "timestamp": str(last["time"]),
    }


# =========================================================
# FORMAT SIGNAL
# =========================================================

def format_signal(result, market):

    call_reasons = result.get(
        "call_reasons",
        []
    )

    put_reasons = result.get(
        "put_reasons",
        []
    )

    call_text = "\n".join(
        f"• {x}" for x in call_reasons
    ) or "• None"

    put_text = "\n".join(
        f"• {x}" for x in put_reasons
    ) or "• None"

    signal = result["signal"]

    if signal == "CALL":
        signal_icon = "🟢"
    elif signal == "PUT":
        signal_icon = "🔴"
    else:
        signal_icon = "⚪"

    return (
        "📊 INDICATOR ANALYSIS\n\n"

        f"💱 Market: {market}\n"
        f"💰 Price: {result['price']:.5f}\n"
        "⏱ Timeframe: 1 minute\n"
        f"🕐 Data: {result['timestamp']} UTC\n\n"

        "📈 EMA\n"
        f"EMA9: {result['ema9']:.5f}\n"
        f"EMA21: {result['ema21']:.5f}\n"
        f"EMA50: {result['ema50']:.5f}\n"
        f"EMA200: {result['ema200']:.5f}\n\n"

        f"📊 RSI: {result['rsi']:.2f}\n\n"

        "📉 MACD\n"
        f"MACD: {result['macd']:.5f}\n"
        f"Signal: {result['macd_signal']:.5f}\n"
        f"Histogram: {result['macd_hist']:.5f}\n\n"

        "〰️ Bollinger\n"
        f"Upper: {result['bb_upper']:.5f}\n"
        f"Middle: {result['bb_middle']:.5f}\n"
        f"Lower: {result['bb_lower']:.5f}\n\n"

        "📐 Stochastic\n"
        f"%K: {result['stoch_k']:.2f}\n"
        f"%D: {result['stoch_d']:.2f}\n\n"

        "💪 ADX\n"
        f"ADX: {result['adx']:.2f}\n"
        f"+DI: {result['plus_di']:.2f}\n"
        f"-DI: {result['minus_di']:.2f}\n\n"

        f"🟢 CALL confirmations: "
        f"{len(call_reasons)}\n"
        f"{call_text}\n\n"

        f"🔴 PUT confirmations: "
        f"{len(put_reasons)}\n"
        f"{put_text}\n\n"

        f"{signal_icon} SIGNAL: {signal}\n"
        f"Confirmations: {result['confirmations']}\n"
        f"Confidence: {result['confidence']}/100\n\n"

        "⚡ Auto-trade: OFF\n"
        "🛑 Martingale: OFF\n\n"

        "⚠️ Indicator signal only. "
        "Not a guaranteed prediction."
    )


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if user_id in authorized_users:

        await update.message.reply_text(
            "🔓 Access authorized\n\n"
            "🤖 Priyanithan Indicator Bot\n"
            "📊 Live Indicator Analysis: READY\n"
            "📈 EMA: READY\n"
            "📊 RSI: READY\n"
            "📉 MACD: READY\n"
            "〰️ Bollinger: READY\n"
            "📐 Stochastic: READY\n"
            "💪 ADX: READY\n"
            "⚡ Auto-trade: OFF\n"
            "🛑 Martingale: OFF\n\n"
            "Use /signal EURUSD"
        )

    else:

        await update.message.reply_text(
            "🔐 Private Access\n\n"
            "/access YOUR_CODE"
        )


# =========================================================
# /ACCESS
# =========================================================

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
            "📊 Indicator Engine: READY\n"
            "⚡ Auto-trade: OFF\n"
            "🛑 Martingale: OFF\n\n"
            "Use /signal EURUSD"
        )

    else:

        await update.message.reply_text(
            "❌ ACCESS DENIED"
        )


# =========================================================
# /SIGNAL
# =========================================================

async def signal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if user_id not in authorized_users:

        await update.message.reply_text(
            "🔒 Access denied."
        )
        return

    if not context.args:

        market = "EURUSD"

    else:

        market = context.args[0].upper()

    pairs = {
        "EURUSD": ("EUR", "USD"),
        "GBPUSD": ("GBP", "USD"),
    }

    if market not in pairs:

        await update.message.reply_text(
            "❌ Unsupported pair.\n\n"
            "Available:\n"
            "/signal EURUSD\n"
            "/signal GBPUSD"
        )
        return

    await update.message.reply_text(
        f"📡 Fetching live {market} 1-minute data..."
    )

    try:

        from_symbol, to_symbol = pairs[market]

        df = get_fx_data(
            from_symbol,
            to_symbol,
            "1min"
        )

        result = analyze_indicators(df)

        text = format_signal(
            result,
            market
        )

        await update.message.reply_text(
            text
        )

    except Exception as e:

        print("Signal error:", e)

        await update.message.reply_text(
            "❌ LIVE MARKET DATA FAILED\n\n"
            f"Market: {market}\n"
            f"Reason: {str(e)}\n\n"
            "No signal generated."
        )


# =========================================================
# /STATUS
# =========================================================

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

    market_status = (
        "CONFIGURED"
        if ALPHA_VANTAGE_API_KEY
        else "NOT CONFIGURED"
    )

    await update.message.reply_text(
        "🟢 Bot Status: ONLINE\n"
        "🔐 Access: AUTHORIZED\n"
        f"📡 Market API: {market_status}\n"
        "📊 Indicator Engine: READY\n"
        "📈 EMA 9/21/50/200: READY\n"
        "📊 RSI: READY\n"
        "📉 MACD: READY\n"
        "〰️ Bollinger: READY\n"
        "📐 Stochastic: READY\n"
        "💪 ADX: READY\n"
        "⚡ Auto-trade: OFF\n"
        "🛑 Martingale: OFF"
    )


# =========================================================
# WEB SERVER
# =========================================================

def run_web():

    port = int(
        os.getenv("PORT", "10000")
    )

    app.run(
        host="0.0.0.0",
        port=port
    )


# =========================================================
# MAIN
# =========================================================

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
        CommandHandler("start", start)
    )

    telegram_app.add_handler(
        CommandHandler("access", access)
    )

    telegram_app.add_handler(
        CommandHandler("status", status)
    )

    telegram_app.add_handler(
        CommandHandler("signal", signal)
    )

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    telegram_app.run_polling()


if __name__ == "__main__":
    main()

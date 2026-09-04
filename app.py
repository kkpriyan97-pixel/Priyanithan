```python
import os
import threading
import time
import requests
import pandas as pd
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import ta


# ============================================================
# ENVIRONMENT
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ACCESS_CODE = os.getenv("ACCESS_CODE", "")

# Yahoo Finance is used for external 1-minute market data.
# No Alpha Vantage API key is required.


# ============================================================
# GLOBALS
# ============================================================

authorized_users = set()

app = Flask(__name__)


# ============================================================
# FLASK HEALTH CHECK
# ============================================================

@app.route("/")
def home():
    return "Priyanithan Indicator Signal Bot is running"


@app.route("/health")
def health():
    return "OK"


# ============================================================
# PAIRS
# ============================================================

pairs = {
    "EURUSD": ("EUR", "USD", "EURUSD=X"),
    "GBPUSD": ("GBP", "USD", "GBPUSD=X"),
}


# ============================================================
# YAHOO FINANCE - 1 MINUTE DATA
# ============================================================

def get_fx_data(from_symbol="EUR", to_symbol="USD", interval="1min"):
    """
    Fetch external 1-minute FX candles from Yahoo Finance.

    EUR/USD -> EURUSD=X
    GBP/USD -> GBPUSD=X

    Returns a pandas DataFrame with:
    open, high, low, close, volume
    """

    symbol = f"{from_symbol}{to_symbol}=X"

    # Yahoo chart API supports interval=1m with recent data.
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    params = {
        "interval": "1m",
        "range": "1d",
        "includePrePost": "false",
        "events": "div,splits"
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

        if "chart" not in data:
            raise Exception("Invalid Yahoo Finance response")

        chart = data["chart"]

        if chart.get("error"):
            raise Exception(str(chart["error"]))

        results = chart.get("result")

        if not results:
            raise Exception("No market data returned")

        result = results[0]

        timestamps = result.get("timestamp")

        if not timestamps:
            raise Exception("No candle timestamps returned")

        indicators = result.get("indicators", {})
        quote_list = indicators.get("quote", [])

        if not quote_list:
            raise Exception("No quote data returned")

        quote = quote_list[0]

        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])
        volumes = quote.get("volume", [])

        length = min(
            len(timestamps),
            len(opens),
            len(highs),
            len(lows),
            len(closes)
        )

        rows = []

        for i in range(length):

            if (
                opens[i] is None
                or highs[i] is None
                or lows[i] is None
                or closes[i] is None
            ):
                continue

            volume = 0

            if i < len(volumes) and volumes[i] is not None:
                volume = volumes[i]

            rows.append({
                "timestamp": pd.to_datetime(
                    timestamps[i],
                    unit="s",
                    utc=True
                ),
                "open": float(opens[i]),
                "high": float(highs[i]),
                "low": float(lows[i]),
                "close": float(closes[i]),
                "volume": float(volume)
            })

        if not rows:
            raise Exception("No valid candles returned")

        df = pd.DataFrame(rows)

        df = df.drop_duplicates(
            subset=["timestamp"]
        )

        df = df.sort_values(
            "timestamp"
        )

        df = df.set_index("timestamp")

        # Remove candles with invalid prices
        df = df[
            (df["close"] > 0) &
            (df["high"] > 0) &
            (df["low"] > 0)
        ]

        # Need enough candles for EMA 200
        if len(df) < 220:
            raise Exception(
                f"Not enough 1-minute candles: {len(df)}"
            )

        return df


# ============================================================
# INDICATOR ANALYSIS
# ============================================================

def analyze_indicators(df):

    if df is None or len(df) < 220:
        return {
            "signal": "NO SIGNAL",
            "confidence": 0,
            "reason": "Not enough candle data"
        }

    data = df.copy()

    close = data["close"]
    high = data["high"]
    low = data["low"]

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    data["ema9"] = ta.trend.ema_indicator(
        close,
        window=9
    )

    data["ema21"] = ta.trend.ema_indicator(
        close,
        window=21
    )

    data["ema50"] = ta.trend.ema_indicator(
        close,
        window=50
    )

    data["ema200"] = ta.trend.ema_indicator(
        close,
        window=200
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    data["rsi"] = ta.momentum.rsi(
        close,
        window=14
    )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    macd = ta.trend.MACD(
        close,
        window_slow=26,
        window_fast=12,
        window_sign=9
    )

    data["macd"] = macd.macd()
    data["macd_signal"] = macd.macd_signal()
    data["macd_hist"] = macd.macd_diff()

    # --------------------------------------------------------
    # BOLLINGER BANDS
    # --------------------------------------------------------

    bb = ta.volatility.BollingerBands(
        close,
        window=20,
        window_dev=2
    )

    data["bb_upper"] = bb.bollinger_hband()
    data["bb_middle"] = bb.bollinger_mavg()
    data["bb_lower"] = bb.bollinger_lband()

    # --------------------------------------------------------
    # STOCHASTIC
    # --------------------------------------------------------

    stoch = ta.momentum.StochasticOscillator(
        high,
        low,
        close,
        window=14,
        smooth_window=3
    )

    data["stoch_k"] = stoch.stoch()
    data["stoch_d"] = stoch.stoch_signal()

    # --------------------------------------------------------
    # ADX
    # --------------------------------------------------------

    adx = ta.trend.ADXIndicator(
        high,
        low,
        close,
        window=14
    )

    data["adx"] = adx.adx()
    data["plus_di"] = adx.adx_pos()
    data["minus_di"] = adx.adx_neg()

    # Remove incomplete indicator rows
    data = data.dropna()

    if len(data) < 10:
        return {
            "signal": "NO SIGNAL",
            "confidence": 0,
            "reason": "Indicator calculation failed"
        }

    # --------------------------------------------------------
    # USE LAST CLOSED CANDLE
    # --------------------------------------------------------

    # Yahoo's latest candle can still be forming.
    # Use the previous candle as the closed candle.
    if len(data) < 2:
        return {
            "signal": "NO SIGNAL",
            "confidence": 0,
            "reason": "No closed candle available"
        }

    current = data.iloc[-2]

    candle_time = data.index[-2]

    price = float(current["close"])

    # --------------------------------------------------------
    # CONFIRMATIONS
    # --------------------------------------------------------

    call_confirmations = []
    put_confirmations = []

    # ========================================================
    # 1. EMA TREND
    # ========================================================

    if (
        current["ema9"] >
        current["ema21"] >
        current["ema50"] >
        current["ema200"]
    ):
        call_confirmations.append(
            "EMA bullish trend"
        )

    elif (
        current["ema9"] <
        current["ema21"] <
        current["ema50"] <
        current["ema200"]
    ):
        put_confirmations.append(
            "EMA bearish trend"
        )

    # ========================================================
    # 2. RSI
    # ========================================================

    rsi_value = float(current["rsi"])

    if 50 < rsi_value < 70:
        call_confirmations.append(
            "RSI bullish"
        )

    elif 30 < rsi_value < 50:
        put_confirmations.append(
            "RSI bearish"
        )

    # ========================================================
    # 3. MACD
    # ========================================================

    macd_value = float(current["macd"])
    macd_signal = float(current["macd_signal"])
    macd_hist = float(current["macd_hist"])

    if (
        macd_value > macd_signal
        and macd_hist > 0
    ):
        call_confirmations.append(
            "MACD bullish"
        )

    elif (
        macd_value < macd_signal
        and macd_hist < 0
    ):
        put_confirmations.append(
            "MACD bearish"
        )

    # ========================================================
    # 4. BOLLINGER BAND
    # ========================================================

    bb_middle = float(current["bb_middle"])

    if price > bb_middle:
        call_confirmations.append(
            "Bollinger bullish"
        )

    elif price < bb_middle:
        put_confirmations.append(
            "Bollinger bearish"
        )

    # ========================================================
    # 5. STOCHASTIC
    # ========================================================

    stoch_k = float(current["stoch_k"])
    stoch_d = float(current["stoch_d"])

    if (
        stoch_k > stoch_d
        and stoch_k < 80
    ):
        call_confirmations.append(
            "Stochastic bullish"
        )

    elif (
        stoch_k < stoch_d
        and stoch_k > 20
    ):
        put_confirmations.append(
            "Stochastic bearish"
        )

    # ========================================================
    # 6. ADX + DI
    # ========================================================

    adx_value = float(current["adx"])
    plus_di = float(current["plus_di"])
    minus_di = float(current["minus_di"])

    if (
        adx_value >= 20
        and plus_di > minus_di
    ):
        call_confirmations.append(
            "ADX bullish"
        )

    elif (
        adx_value >= 20
        and minus_di > plus_di
    ):
        put_confirmations.append(
            "ADX bearish"
        )

    # ========================================================
    # FINAL DECISION
    # ========================================================

    call_count = len(call_confirmations)
    put_count = len(put_confirmations)

    total_indicators = 6

    signal = "NO SIGNAL"
    confidence = 0

    if (
        call_count >= 4
        and call_count > put_count
    ):
        signal = "CALL"
        confidence = round(
            (call_count / total_indicators) * 100,
            1
        )

    elif (
        put_count >= 4
        and put_count > call_count
    ):
        signal = "PUT"
        confidence = round(
            (put_count / total_indicators) * 100,
            1
        )

    return {
        "signal": signal,
        "confidence": confidence,
        "price": price,
        "candle_time": candle_time,
        "rsi": rsi_value,
        "macd": macd_value,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "ema9": float(current["ema9"]),
        "ema21": float(current["ema21"]),
        "ema50": float(current["ema50"]),
        "ema200": float(current["ema200"]),
        "bb_upper": float(current["bb_upper"]),
        "bb_middle": bb_middle,
        "bb_lower": float(current["bb_lower"]),
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
        "adx": adx_value,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "call_count": call_count,
        "put_count": put_count,
        "call_confirmations": call_confirmations,
        "put_confirmations": put_confirmations
    }


# ============================================================
# TELEGRAM AUTHORIZATION
# ============================================================

def is_authorized(user_id):

    return user_id in authorized_users


# ============================================================
# /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    await update.message.reply_text(
        "🤖 Priyanithan Indicator Signal Bot\n\n"
        "📊 1-Minute Indicator Engine\n"
        "💱 EURUSD / GBPUSD\n"
        "📈 EMA • RSI • MACD • BB • Stochastic • ADX\n\n"
        "🔐 Use /access YOUR_CODE to authorize."
    )


# ============================================================
# /ACCESS
# ============================================================

async def access_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    user_id = user.id

    if not ACCESS_CODE:
        await update.message.reply_text(
            "❌ ACCESS_CODE is not configured on the server."
        )
        return

    if not context.args:

        await update.message.reply_text(
            "🔐 Usage:\n"
            "/access YOUR_CODE"
        )

        return

    supplied_code = context.args[0]

    if supplied_code == ACCESS_CODE:

        authorized_users.add(user_id)

        await update.message.reply_text(
            "✅ Access authorized.\n\n"
            "📊 Indicator engine: READY\n"
            "📡 Market data: Yahoo Finance 1-minute\n"
            "⚡ Auto-trade: OFF\n"
            "🛑 Martingale: OFF"
        )

    else:

        await update.message.reply_text(
            "❌ Invalid access code."
        )


# ============================================================
# /STATUS
# ============================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if not is_authorized(user.id):

        await update.message.reply_text(
            "🔐 Authorization required.\n\n"
            "Use /access YOUR_CODE"
        )

        return

    await update.message.reply_text(
        "🟢 BOT STATUS: ONLINE\n\n"
        "🔐 Access: AUTHORIZED\n"
        "📊 Indicator Engine: READY\n"
        "📈 EMA 9/21/50/200: READY\n"
        "📊 RSI 14: READY\n"
        "📉 MACD: READY\n"
        "〽️ Bollinger Bands: READY\n"
        "📊 Stochastic: READY\n"
        "📈 ADX: READY\n"
        "📡 Market Data: Yahoo Finance 1-minute\n"
        "💱 EURUSD: READY\n"
        "💷 GBPUSD: READY\n"
        "⚡ Auto-trade: OFF\n"
        "🛑 Martingale: OFF"
    )


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(
    market,
    analysis
):

    signal = analysis.get("signal", "NO SIGNAL")

    confidence = analysis.get(
        "confidence",
        0
    )

    price = analysis.get(
        "price",
        0
    )

    candle_time = analysis.get(
        "candle_time",
        "-"
    )

    call_count = analysis.get(
        "call_count",
        0
    )

    put_count = analysis.get(
        "put_count",
        0
    )

    call_confirmations = analysis.get(
        "call_confirmations",
        []
    )

    put_confirmations = analysis.get(
        "put_confirmations",
        []
    )

    if signal == "CALL":

        direction = "🟢 CALL"

        confirmation_text = "\n".join(
            f"  ✅ {item}"
            for item in call_confirmations
        )

    elif signal == "PUT":

        direction = "🔴 PUT"

        confirmation_text = "\n".join(
            f"  ✅ {item}"
            for item in put_confirmations
        )

    else:

        direction = "⚪ NO SIGNAL"

        confirmation_text = (
            "  CALL confirmations: "
            f"{call_count}\n"
            "  PUT confirmations: "
            f"{put_count}"
        )

    return (
        "📊 INDICATOR ANALYSIS\n\n"
        f"💱 Market: {market}\n"
        f"💰 Price: {price:.5f}\n"
        "⏱ Timeframe: 1 minute\n"
        f"🕐 Closed candle: {candle_time}\n\n"

        f"EMA9: {analysis.get('ema9', 0):.5f}\n"
        f"EMA21: {analysis.get('ema21', 0):.5f}\n"
        f"EMA50: {analysis.get('ema50', 0):.5f}\n"
        f"EMA200: {analysis.get('ema200', 0):.5f}\n\n"

        f"RSI: {analysis.get('rsi', 0):.2f}\n"
        f"MACD: {analysis.get('macd', 0):.5f}\n"
        f"Signal: {analysis.get('macd_signal', 0):.5f}\n"
        f"Histogram: {analysis.get('macd_hist', 0):.5f}\n\n"

        f"Bollinger Upper: "
        f"{analysis.get('bb_upper', 0):.5f}\n"
        f"Bollinger Middle: "
        f"{analysis.get('bb_middle', 0):.5f}\n"
        f"Bollinger Lower: "
        f"{analysis.get('bb_lower', 0):.5f}\n\n"

        f"Stoch K: {analysis.get('stoch_k', 0):.2f}\n"
        f"Stoch D: {analysis.get('stoch_d', 0):.2f}\n\n"

        f"ADX: {analysis.get('adx', 0):.2f}\n"
        f"+DI: {analysis.get('plus_di', 0):.2f}\n"
        f"-DI: {analysis.get('minus_di', 0):.2f}\n\n"

        f"🟢 CALL confirmations: {call_count}\n"
        f"🔴 PUT confirmations: {put_count}\n\n"

        f"🎯 SIGNAL: {direction}\n"
        f"📊 Indicator score: {confidence}%\n\n"

        f"Confirmations:\n"
        f"{confirmation_text}\n\n"

        "📡 Data source: Yahoo Finance\n"
        "⚠️ External market data — not Olymp Trade's official quote feed.\n"
        "⚡ Auto-trade: OFF"
    )


# ============================================================
# /SIGNAL
# ============================================================

async def signal_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if not is_authorized(user.id):

        await update.message.reply_text(
            "🔐 Authorization required.\n\n"
            "Use /access YOUR_CODE"
        )

        return

    if not context.args:

        await update.message.reply_text(
            "📌 Usage:\n\n"
            "/signal EURUSD\n"
            "/signal GBPUSD"
        )

        return

    market = context.args[0].upper()

    if market not in pairs:

        await update.message.reply_text(
            "❌ Unsupported pair.\n\n"
            "Available:\n"
            "/signal EURUSD\n"
            "/signal GBPUSD"
        )

        return

    from_symbol, to_symbol, yahoo_symbol = pairs[market]

    loading_message = await update.message.reply_text(
        f"📡 Fetching live {market} 1-minute data..."
    )

    try:

        df = get_fx_data(
            from_symbol,
            to_symbol,
            "1min"
        )

        analysis = analyze_indicators(df)

        if analysis.get("signal") == "NO SIGNAL":

            await loading_message.edit_text(
                format_signal(
                    market,
                    analysis
                )
            )

            return

        await loading_message.edit_text(
            format_signal(
                market,
                analysis
            )
        )

    except Exception as e:

        error_message = str(e)

        # Avoid sending excessively long server errors to Telegram.
        if len(error_message) > 500:
            error_message = error_message[:500] + "..."

        await loading_message.edit_text(
            "❌ LIVE MARKET DATA FAILED\n\n"
            f"Market: {market}\n\n"
            f"Reason: {error_message}\n\n"
            "No signal generated."
        )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    print(
        "Telegram error:",
        context.error
    )


# ============================================================
# START FLASK SERVER
# ============================================================

def run_flask():

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not TELEGRAM_BOT_TOKEN:

        print(
            "❌ TELEGRAM_BOT_TOKEN is not configured"
        )

        return

    print(
        "======================================"
    )

    print(
        "Priyanithan Indicator Signal Bot"
    )

    print(
        "======================================"
    )

    print(
        "Market data: Yahoo Finance"
    )

    print(
        "Timeframe: 1 minute"
    )

    print(
        "Pairs: EURUSD / GBPUSD"
    )

    print(
        "Auto-trade: OFF"
    )

    print(
        "Martingale: OFF"
    )

    print(
        "AI: OFF"
    )

    print(
        "======================================"
    )

    # Flask web server
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    # Telegram application
    telegram_app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    telegram_app.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "access",
            access_command
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "status",
            status_command
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "signal",
            signal_command
        )
    )

    telegram_app.add_error_handler(
        error_handler
    )

    print(
        "🤖 Telegram polling started..."
    )

    telegram_app.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
```

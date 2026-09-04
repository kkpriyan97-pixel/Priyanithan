import os
import math
import threading
from datetime import datetime, timezone

import requests
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# =========================================================
# CONFIG
# =========================================================

app = Flask(__name__)

ACCESS_CODE = os.environ.get("ACCESS_CODE", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# Yahoo Finance symbols
MARKETS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",

    # Set this in Render Environment if you know the
    # correct Yahoo Finance symbol for your Asia Composite.
    "ASIA": os.environ.get(
        "ASIA_COMPOSITE_SYMBOL",
        ""
    ),
}

authorized_users = set()

REQUEST_TIMEOUT = 15

MIN_CANDLES = 200


# =========================================================
# WEB
# =========================================================

@app.route("/")
def home():
    return "Priyanithan Indicator Bot is running"


# =========================================================
# MARKET DATA
# =========================================================

def get_candles(symbol, interval="1m", range_value="1d"):
    """
    Get OHLC candles from Yahoo Finance chart endpoint.

    This is NOT Olymp Trade's own market feed.
    """

    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + symbol
    )

    params = {
        "interval": interval,
        "range": range_value,
        "includePrePost": "true",
        "events": "div,splits",
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/131.0 Safari/537.36"
        )
    }

    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    chart = data.get("chart", {})
    result = chart.get("result")

    if not result:
        raise ValueError(
            "Market data unavailable"
        )

    result = result[0]

    timestamps = result.get("timestamp", [])
    quote_list = (
        result
        .get("indicators", {})
        .get("quote", [])
    )

    if not quote_list:
        raise ValueError(
            "OHLC data unavailable"
        )

    quote = quote_list[0]

    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])

    candles = []

    for i in range(len(timestamps)):

        try:
            o = opens[i]
            h = highs[i]
            l = lows[i]
            c = closes[i]

            if (
                o is None
                or h is None
                or l is None
                or c is None
            ):
                continue

            candles.append(
                {
                    "timestamp": timestamps[i],
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                }
            )

        except (
            IndexError,
            TypeError,
            ValueError,
        ):
            continue

    if len(candles) < MIN_CANDLES:
        raise ValueError(
            f"Only {len(candles)} candles received. "
            f"Need at least {MIN_CANDLES}."
        )

    return candles


# =========================================================
# INDICATOR FUNCTIONS
# =========================================================

def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2.0 / (period + 1.0)

    value = sum(
        values[:period]
    ) / period

    for price in values[period:]:
        value = (
            (price - value) * multiplier
            + value
        )

    return value


def rsi(values, period=14):
    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):

        change = (
            values[i]
            - values[i - 1]
        )

        gains.append(
            max(change, 0.0)
        )

        losses.append(
            max(-change, 0.0)
        )

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (avg_gain * (period - 1))
            + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1))
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100.0 - (
        100.0 / (1.0 + rs)
    )


def macd(values):
    if len(values) < 35:
        return None, None, None

    # Standard MACD
    fast = ema(values, 12)
    slow = ema(values, 26)

    if fast is None or slow is None:
        return None, None, None

    macd_line = fast - slow

    # Build MACD history for signal line
    macd_history = []

    for i in range(26, len(values) + 1):

        subset = values[:i]

        fast_i = ema(
            subset,
            12
        )

        slow_i = ema(
            subset,
            26
        )

        if (
            fast_i is not None
            and slow_i is not None
        ):
            macd_history.append(
                fast_i - slow_i
            )

    if len(macd_history) < 9:
        return (
            macd_line,
            None,
            None
        )

    signal_line = ema(
        macd_history,
        9
    )

    if signal_line is None:
        return (
            macd_line,
            None,
            None
        )

    histogram = (
        macd_line
        - signal_line
    )

    return (
        macd_line,
        signal_line,
        histogram
    )


def bollinger(values, period=20, deviations=2):
    if len(values) < period:
        return (
            None,
            None,
            None
        )

    window = values[-period:]

    middle = (
        sum(window)
        / period
    )

    variance = (
        sum(
            (x - middle) ** 2
            for x in window
        )
        / period
    )

    std = math.sqrt(variance)

    upper = (
        middle
        + deviations * std
    )

    lower = (
        middle
        - deviations * std
    )

    return (
        upper,
        middle,
        lower
    )


def stochastic(
    highs,
    lows,
    closes,
    period=14,
    smooth=3
):
    if len(closes) < period:
        return None, None

    k_values = []

    start = max(
        0,
        len(closes) - 20
    )

    for i in range(
        start,
        len(closes)
    ):

        if i + 1 < period:
            continue

        high_window = highs[
            i - period + 1:i + 1
        ]

        low_window = lows[
            i - period + 1:i + 1
        ]

        highest = max(
            high_window
        )

        lowest = min(
            low_window
        )

        if highest == lowest:
            k = 50.0
        else:
            k = (
                (
                    closes[i]
                    - lowest
                )
                / (
                    highest
                    - lowest
                )
            ) * 100.0

        k_values.append(k)

    if not k_values:
        return None, None

    k = k_values[-1]

    if len(k_values) >= smooth:
        d = (
            sum(k_values[-smooth:])
            / smooth
        )
    else:
        d = k

    return k, d


def adx(
    highs,
    lows,
    closes,
    period=14
):
    if len(closes) < period * 2 + 1:
        return None, None, None

    true_ranges = []
    plus_dm = []
    minus_dm = []

    for i in range(1, len(closes)):

        high = highs[i]
        low = lows[i]

        previous_high = highs[i - 1]
        previous_low = lows[i - 1]
        previous_close = closes[i - 1]

        tr = max(
            high - low,
            abs(
                high
                - previous_close
            ),
            abs(
                low
                - previous_close
            )
        )

        up_move = (
            high
            - previous_high
        )

        down_move = (
            previous_low
            - low
        )

        plus = (
            up_move
            if (
                up_move > down_move
                and up_move > 0
            )
            else 0.0
        )

        minus = (
            down_move
            if (
                down_move > up_move
                and down_move > 0
            )
            else 0.0
        )

        true_ranges.append(tr)
        plus_dm.append(plus)
        minus_dm.append(minus)

    if len(true_ranges) < period:
        return None, None, None

    atr = (
        sum(true_ranges[:period])
        / period
    )

    plus_avg = (
        sum(plus_dm[:period])
        / period
    )

    minus_avg = (
        sum(minus_dm[:period])
        / period
    )

    dx_values = []

    for i in range(
        period,
        len(true_ranges)
    ):

        atr = (
            (
                atr * (period - 1)
            )
            + true_ranges[i]
        ) / period

        plus_avg = (
            (
                plus_avg * (period - 1)
            )
            + plus_dm[i]
        ) / period

        minus_avg = (
            (
                minus_avg * (period - 1)
            )
            + minus_dm[i]
        ) / period

        if atr == 0:
            plus_di = 0
            minus_di = 0
        else:
            plus_di = (
                100
                * plus_avg
                / atr
            )

            minus_di = (
                100
                * minus_avg
                / atr
            )

        denominator = (
            plus_di
            + minus_di
        )

        if denominator == 0:
            dx = 0
        else:
            dx = (
                100
                * abs(
                    plus_di
                    - minus_di
                )
                / denominator
            )

        dx_values.append(
            (
                dx,
                plus_di,
                minus_di
            )
        )

    if len(dx_values) < period:
        return None, None, None

    adx_value = (
        sum(
            x[0]
            for x in dx_values[:period]
        )
        / period
    )

    for i in range(
        period,
        len(dx_values)
    ):

        adx_value = (
            (
                adx_value
                * (period - 1)
            )
            + dx_values[i][0]
        ) / period

    plus_di = dx_values[-1][1]
    minus_di = dx_values[-1][2]

    return (
        adx_value,
        plus_di,
        minus_di
    )


# =========================================================
# INDICATOR ANALYSIS
# =========================================================

def analyze_candles(candles):

    closes = [
        x["close"]
        for x in candles
    ]

    highs = [
        x["high"]
        for x in candles
    ]

    lows = [
        x["low"]
        for x in candles
    ]

    opens = [
        x["open"]
        for x in candles
    ]

    price = closes[-1]

    # -------------------------
    # EMA
    # -------------------------

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)

    # -------------------------
    # RSI
    # -------------------------

    rsi_value = rsi(
        closes,
        14
    )

    # -------------------------
    # MACD
    # -------------------------

    macd_line, macd_signal, macd_hist = macd(
        closes
    )

    # -------------------------
    # Bollinger
    # -------------------------

    bb_upper, bb_middle, bb_lower = bollinger(
        closes,
        20,
        2
    )

    # -------------------------
    # Stochastic
    # -------------------------

    stoch_k, stoch_d = stochastic(
        highs,
        lows,
        closes,
        14,
        3
    )

    # -------------------------
    # ADX
    # -------------------------

    adx_value, plus_di, minus_di = adx(
        highs,
        lows,
        closes,
        14
    )

    call = 0
    put = 0

    reasons_call = []
    reasons_put = []

    # =====================================================
    # EMA CONFIRMATION
    # =====================================================

    if all(
        x is not None
        for x in (
            ema9,
            ema21,
            ema50,
            ema200
        )
    ):

        if (
            ema9
            > ema21
            > ema50
            > ema200
        ):
            call += 1
            reasons_call.append(
                "EMA bullish trend"
            )

        elif (
            ema9
            < ema21
            < ema50
            < ema200
        ):
            put += 1
            reasons_put.append(
                "EMA bearish trend"
            )

    # =====================================================
    # RSI
    # =====================================================

    if rsi_value is not None:

        if (
            rsi_value > 50
            and rsi_value < 70
        ):
            call += 1
            reasons_call.append(
                f"RSI bullish ({rsi_value:.1f})"
            )

        elif (
            rsi_value < 50
            and rsi_value > 30
        ):
            put += 1
            reasons_put.append(
                f"RSI bearish ({rsi_value:.1f})"
            )

    # =====================================================
    # MACD
    # =====================================================

    if (
        macd_line is not None
        and macd_signal is not None
        and macd_hist is not None
    ):

        if (
            macd_line > macd_signal
            and macd_hist > 0
        ):
            call += 1
            reasons_call.append(
                "MACD bullish"
            )

        elif (
            macd_line < macd_signal
            and macd_hist < 0
        ):
            put += 1
            reasons_put.append(
                "MACD bearish"
            )

    # =====================================================
    # BOLLINGER
    # =====================================================

    if (
        bb_upper is not None
        and bb_middle is not None
        and bb_lower is not None
    ):

        if (
            price > bb_middle
            and price < bb_upper
        ):
            call += 1
            reasons_call.append(
                "Bollinger bullish"
            )

        elif (
            price < bb_middle
            and price > bb_lower
        ):
            put += 1
            reasons_put.append(
                "Bollinger bearish"
            )

    # =====================================================
    # STOCHASTIC
    # =====================================================

    if (
        stoch_k is not None
        and stoch_d is not None
    ):

        if (
            stoch_k > stoch_d
            and stoch_k < 80
        ):
            call += 1
            reasons_call.append(
                "Stochastic bullish"
            )

        elif (
            stoch_k < stoch_d
            and stoch_k > 20
        ):
            put += 1
            reasons_put.append(
                "Stochastic bearish"
            )

    # =====================================================
    # ADX
    # =====================================================

    if (
        adx_value is not None
        and plus_di is not None
        and minus_di is not None
    ):

        # ADX >= 20 means trend has some strength.
        if (
            adx_value >= 20
            and plus_di > minus_di
        ):
            call += 1
            reasons_call.append(
                f"ADX bullish ({adx_value:.1f})"
            )

        elif (
            adx_value >= 20
            and minus_di > plus_di
        ):
            put += 1
            reasons_put.append(
                f"ADX bearish ({adx_value:.1f})"
            )

    # =====================================================
    # MOMENTUM
    # =====================================================

    if len(closes) >= 4:

        if (
            closes[-1]
            > closes[-2]
            > closes[-3]
        ):
            call += 1
            reasons_call.append(
                "Price momentum bullish"
            )

        elif (
            closes[-1]
            < closes[-2]
            < closes[-3]
        ):
            put += 1
            reasons_put.append(
                "Price momentum bearish"
            )

    # =====================================================
    # CANDLE
    # =====================================================

    if opens[-1] < closes[-1]:

        call += 1
        reasons_call.append(
            "Latest candle bullish"
        )

    elif opens[-1] > closes[-1]:

        put += 1
        reasons_put.append(
            "Latest candle bearish"
        )

    # =====================================================
    # FINAL SIGNAL
    # =====================================================

    total_confirmations = max(
        call,
        put
    )

    # Need at least 4 confirmations.
    if (
        call >= 4
        and call > put
    ):
        signal = "CALL"

    elif (
        put >= 4
        and put > call
    ):
        signal = "PUT"

    else:
        signal = "NO SIGNAL"

    return {
        "signal": signal,
        "price": price,
        "call": call,
        "put": put,
        "confirmations": total_confirmations,

        "ema9": ema9,
        "ema21": ema21,
        "ema50": ema50,
        "ema200": ema200,

        "rsi": rsi_value,

        "macd": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,

        "bb_upper": bb_upper,
        "bb_middle": bb_middle,
        "bb_lower": bb_lower,

        "stoch_k": stoch_k,
        "stoch_d": stoch_d,

        "adx": adx_value,
        "plus_di": plus_di,
        "minus_di": minus_di,

        "call_reasons": reasons_call,
        "put_reasons": reasons_put,
    }


# =========================================================
# FORMAT SIGNAL
# =========================================================

def format_signal(
    market_name,
    result
):

    signal = result["signal"]

    if signal == "CALL":
        emoji = "🟢"
    elif signal == "PUT":
        emoji = "🔴"
    else:
        emoji = "⚪"

    def f(value, digits=2):

        if value is None:
            return "N/A"

        return f"{value:.{digits}f}"

    call_reasons = (
        "\n".join(
            "• " + x
            for x in result[
                "call_reasons"
            ]
        )
        or "None"
    )

    put_reasons = (
        "\n".join(
            "• " + x
            for x in result[
                "put_reasons"
            ]
        )
        or "None"
    )

    now = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    return (
        f"📊 INDICATOR ANALYSIS\n\n"

        f"💱 Market: {market_name}\n"
        f"💰 Price: {f(result['price'], 5)}\n"
        f"⏱ Timeframe: 1 minute\n"
        f"🕐 Data: {now}\n\n"

        f"📈 EMA\n"
        f"EMA9: {f(result['ema9'], 5)}\n"
        f"EMA21: {f(result['ema21'], 5)}\n"
        f"EMA50: {f(result['ema50'], 5)}\n"
        f"EMA200: {f(result['ema200'], 5)}\n\n"

        f"📊 RSI: {f(result['rsi'])}\n\n"

        f"📉 MACD\n"
        f"MACD: {f(result['macd'], 5)}\n"
        f"Signal: {f(result['macd_signal'], 5)}\n"
        f"Histogram: {f(result['macd_hist'], 5)}\n\n"

        f"〰️ Bollinger\n"
        f"Upper: {f(result['bb_upper'], 5)}\n"
        f"Middle: {f(result['bb_middle'], 5)}\n"
        f"Lower: {f(result['bb_lower'], 5)}\n\n"

        f"📐 Stochastic\n"
        f"%K: {f(result['stoch_k'])}\n"
        f"%D: {f(result['stoch_d'])}\n\n"

        f"💪 ADX: {f(result['adx'])}\n"
        f"+DI: {f(result['plus_di'])}\n"
        f"-DI: {f(result['minus_di'])}\n\n"

        f"🟢 CALL confirmations: "
        f"{result['call']}\n"
        f"{call_reasons}\n\n"

        f"🔴 PUT confirmations: "
        f"{result['put']}\n"
        f"{put_reasons}\n\n"

        f"{emoji} SIGNAL: {signal}\n"
        f"Confirmations: "
        f"{result['confirmations']}\n\n"

        f"⚡ Auto-trade: OFF\n"
        f"🛑 Martingale: OFF\n\n"

        f"⚠️ Indicator signal only. "
        f"Not a guaranteed prediction."
    )


# =========================================================
# ACCESS CHECK
# =========================================================

def is_authorized(update):

    user = update.effective_user

    if not user:
        return False

    return user.id in authorized_users


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if is_authorized(update):

        await update.message.reply_text(
            "🔓 ACCESS AUTHORIZED\n\n"
            "🤖 Priyanithan Indicator Bot\n\n"
            "📊 EMA: READY\n"
            "📈 RSI: READY\n"
            "📉 MACD: READY\n"
            "〰️ Bollinger: READY\n"
            "📐 Stochastic: READY\n"
            "💪 ADX: READY\n"
            "💱 Live market data: READY\n\n"
            "Commands:\n"
            "/signal EURUSD\n"
            "/signal GBPUSD\n"
            "/signal ASIA\n"
            "/status\n\n"
            "⚡ Auto-trade: OFF\n"
            "🛑 Martingale: OFF"
        )

    else:

        await update.message.reply_text(
            "🔐 Private Access\n\n"
            "Use:\n"
            "/access YOUR_CODE"
        )


# =========================================================
# /ACCESS
# =========================================================

async def access(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not ACCESS_CODE:

        await update.message.reply_text(
            "⚠️ ACCESS_CODE is not configured."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Usage:\n"
            "/access YOUR_CODE"
        )

        return

    supplied_code = context.args[0]

    if supplied_code == ACCESS_CODE:

        authorized_users.add(
            update.effective_user.id
        )

        await update.message.reply_text(
            "✅ ACCESS GRANTED\n\n"
            "📊 Indicator Engine: READY\n"
            "📈 EMA: READY\n"
            "📊 RSI: READY\n"
            "📉 MACD: READY\n"
            "〰️ Bollinger: READY\n"
            "📐 Stochastic: READY\n"
            "💪 ADX: READY\n\n"
            "Use:\n"
            "/signal EURUSD\n"
            "/signal GBPUSD\n"
            "/signal ASIA\n\n"
            "⚡ Auto-trade: OFF\n"
            "🛑 Martingale: OFF"
        )

    else:

        await update.message.reply_text(
            "❌ ACCESS DENIED"
        )


# =========================================================
# /SIGNAL
# =========================================================

async def signal_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_authorized(update):

        await update.message.reply_text(
            "🔒 Access denied.\n"
            "Use /access YOUR_CODE"
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Usage:\n\n"
            "/signal EURUSD\n"
            "/signal GBPUSD\n"
            "/signal ASIA"
        )

        return

    market = (
        context.args[0]
        .upper()
        .replace("/", "")
        .replace("-", "")
    )

    if market not in MARKETS:

        await update.message.reply_text(
            "❌ Unknown market.\n\n"
            "Available:\n"
            "EURUSD\n"
            "GBPUSD\n"
            "ASIA"
        )

        return

    symbol = MARKETS[market]

    if not symbol:

        await update.message.reply_text(
            "⚠️ ASIA_COMPOSITE_SYMBOL "
            "is not configured.\n\n"
            "Render → Environment → Add:\n"
            "ASIA_COMPOSITE_SYMBOL\n\n"
            "Use the correct Yahoo Finance "
            "symbol for your Asia Composite."
        )

        return

    await update.message.reply_text(
        f"🔎 {market} 1-minute candles "
        "fetching...\n"
        "📊 Calculating indicators..."
    )

    try:

        candles = get_candles(
            symbol,
            interval="1m",
            range_value="1d"
        )

        result = analyze_candles(
            candles
        )

        message = format_signal(
            market,
            result
        )

        await update.message.reply_text(
            message
        )

    except requests.RequestException as e:

        print(
            "Market request error:",
            repr(e)
        )

        await update.message.reply_text(
            f"❌ {market}: MARKET DATA FAILED\n\n"
            "The external market-data source "
            "did not respond.\n\n"
            "No signal generated."
        )

    except Exception as e:

        print(
            "Signal error:",
            repr(e)
        )

        await update.message.reply_text(
            f"❌ {market}: ANALYSIS FAILED\n\n"
            "No signal generated."
        )


# =========================================================
# /STATUS
# =========================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_authorized(update):

        await update.message.reply_text(
            "🔒 Access denied."
        )

        return

    asia_status = (
        "CONFIGURED"
        if MARKETS["ASIA"]
        else "NOT CONFIGURED"
    )

    await update.message.reply_text(

        "🟢 Bot Status: ONLINE\n"
        "🔐 Access: AUTHORIZED\n\n"

        "📊 Indicator Engine: READY\n"
        "📈 EMA 9/21/50/200: READY\n"
        "📊 RSI 14: READY\n"
        "📉 MACD: READY\n"
        "〰️ Bollinger: READY\n"
        "📐 Stochastic: READY\n"
        "💪 ADX: READY\n\n"

        "💱 EUR/USD: AVAILABLE\n"
        "💷 GBP/USD: AVAILABLE\n"
        f"🌏 Asia Composite: {asia_status}\n\n"

        "📡 Data source: External market feed\n"
        "⏱ Timeframe: 1 minute\n\n"

        "⚡ Auto-trade: OFF\n"
        "🛑 Martingale: OFF\n"
        "🤖 AI: OFF"
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update,
    context
):

    print(
        "Telegram error:",
        repr(context.error)
    )


# =========================================================
# WEB SERVER
# =========================================================

def run_web():

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
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
            "TELEGRAM_BOT_TOKEN "
            "is not configured"
        )

        return

    telegram_app = (
        Application
        .builder()
        .token(
            TELEGRAM_BOT_TOKEN
        )
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
            "signal",
            signal_command
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "status",
            status
        )
    )

    telegram_app.add_error_handler(
        error_handler
    )

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    print(
        "Priyanithan Indicator Bot started."
    )

    telegram_app.run_polling()


if __name__ == "__main__":
    main()

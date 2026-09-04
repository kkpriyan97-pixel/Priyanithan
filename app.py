````python
import os
import threading
import requests
import json
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


# =========================
# CONFIG
# =========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ACCESS_CODE = os.getenv("ACCESS_CODE")

# Airforce AI
AIRFORCE_API_KEY = os.getenv("AIRFORCE_API_KEY")
AIRFORCE_MODEL = os.getenv(
    "AIRFORCE_MODEL",
    "gpt-4.1-mini"
)
AIRFORCE_URL = (
    "https://api.airforce/v1/chat/completions"
)

authorized_users = set()

app = Flask(__name__)


# =========================
# WEB SERVER
# =========================

@app.route("/")
def home():
    return "Priyanithan AI Indicator Bot is ONLINE"


@app.route("/health")
def health():
    return "OK"


# =========================
# MARKET PAIRS
# =========================

PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
}


# =========================
# GET MARKET DATA
# =========================

def get_fx_data(pair):

    symbol = PAIRS.get(pair.upper())

    if not symbol:
        return None, "Unsupported pair"

    url = (
        "https://query1.finance.yahoo.com/"
        f"v8/finance/chart/{symbol}"
    )

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

        chart = response.json().get(
            "chart",
            {}
        )

        result = chart.get("result")

        if not result:
            return None, "No market data"

        result = result[0]

        timestamps = result.get("timestamp")

        indicators = result.get(
            "indicators",
            {}
        )

        quote_list = indicators.get(
            "quote",
            []
        )

        if not timestamps or not quote_list:
            return None, "Incomplete market data"

        quote = quote_list[0]

        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("close"),
            "volume": quote.get("volume"),
        })

        df = df.dropna(
            subset=[
                "open",
                "high",
                "low",
                "close"
            ]
        ).copy()

        if len(df) < 220:
            return None, (
                f"Not enough candles: {len(df)}"
            )

        return df, None

    except Exception as e:

        return None, str(e)


# =========================
# INDICATOR ANALYSIS
# =========================

def analyze_indicators(data, pair):

    try:

        close = data["close"]
        high = data["high"]
        low = data["low"]

        # =========================
        # EMA
        # =========================

        ema9 = EMAIndicator(
            close=close,
            window=9
        ).ema_indicator()

        ema21 = EMAIndicator(
            close=close,
            window=21
        ).ema_indicator()

        ema50 = EMAIndicator(
            close=close,
            window=50
        ).ema_indicator()

        ema200 = EMAIndicator(
            close=close,
            window=200
        ).ema_indicator()

        # =========================
        # RSI
        # =========================

        rsi = RSIIndicator(
            close=close,
            window=14
        ).rsi()

        # =========================
        # MACD
        # =========================

        macd_indicator = MACD(
            close=close,
            window_slow=26,
            window_fast=12,
            window_sign=9
        )

        macd_line = macd_indicator.macd()
        macd_signal = (
            macd_indicator.macd_signal()
        )
        macd_histogram = (
            macd_indicator.macd_diff()
        )

        # =========================
        # BOLLINGER BANDS
        # =========================

        bb = BollingerBands(
            close=close,
            window=20,
            window_dev=2
        )

        bb_upper = bb.bollinger_hband()
        bb_middle = bb.bollinger_mavg()
        bb_lower = bb.bollinger_lband()

        # =========================
        # STOCHASTIC
        # =========================

        stoch = StochasticOscillator(
            high=high,
            low=low,
            close=close,
            window=14,
            smooth_window=3
        )

        stoch_k = stoch.stoch()
        stoch_d = stoch.stoch_signal()

        # =========================
        # ADX
        # =========================

        adx_indicator = ADXIndicator(
            high=high,
            low=low,
            close=close,
            window=14
        )

        adx = adx_indicator.adx()
        plus_di = adx_indicator.adx_pos()
        minus_di = adx_indicator.adx_neg()

        # =========================
        # LAST CLOSED CANDLE
        # =========================

        candle = data.iloc[-2]
        idx = len(data) - 2

        price = float(candle["close"])

        values = {
            "ema9": float(ema9.iloc[idx]),
            "ema21": float(ema21.iloc[idx]),
            "ema50": float(ema50.iloc[idx]),
            "ema200": float(ema200.iloc[idx]),

            "rsi": float(rsi.iloc[idx]),

            "macd": float(
                macd_line.iloc[idx]
            ),
            "macd_signal": float(
                macd_signal.iloc[idx]
            ),
            "macd_histogram": float(
                macd_histogram.iloc[idx]
            ),

            "bb_upper": float(
                bb_upper.iloc[idx]
            ),
            "bb_middle": float(
                bb_middle.iloc[idx]
            ),
            "bb_lower": float(
                bb_lower.iloc[idx]
            ),

            "stoch_k": float(
                stoch_k.iloc[idx]
            ),
            "stoch_d": float(
                stoch_d.iloc[idx]
            ),

            "adx": float(
                adx.iloc[idx]
            ),
            "plus_di": float(
                plus_di.iloc[idx]
            ),
            "minus_di": float(
                minus_di.iloc[idx]
            ),
        }

        call_confirmations = []
        put_confirmations = []

        # =========================
        # EMA TREND
        # =========================

        if (
            values["ema9"] > values["ema21"]
            and values["ema21"] > values["ema50"]
            and values["ema50"] > values["ema200"]
        ):

            call_confirmations.append(
                "EMA bullish trend"
            )

        elif (
            values["ema9"] < values["ema21"]
            and values["ema21"] < values["ema50"]
            and values["ema50"] < values["ema200"]
        ):

            put_confirmations.append(
                "EMA bearish trend"
            )

        # =========================
        # RSI
        # =========================

        if values["rsi"] > 50:

            call_confirmations.append(
                "RSI bullish"
            )

        elif values["rsi"] < 50:

            put_confirmations.append(
                "RSI bearish"
            )

        # =========================
        # MACD
        # =========================

        if (
            values["macd"]
            > values["macd_signal"]
            and values["macd_histogram"] > 0
        ):

            call_confirmations.append(
                "MACD bullish"
            )

        elif (
            values["macd"]
            < values["macd_signal"]
            and values["macd_histogram"] < 0
        ):

            put_confirmations.append(
                "MACD bearish"
            )

        # =========================
        # BOLLINGER
        # =========================

        if price > values["bb_middle"]:

            call_confirmations.append(
                "Bollinger bullish"
            )

        elif price < values["bb_middle"]:

            put_confirmations.append(
                "Bollinger bearish"
            )

        # =========================
        # STOCHASTIC
        # =========================

        if (
            values["stoch_k"]
            > values["stoch_d"]
        ):

            call_confirmations.append(
                "Stochastic bullish"
            )

        elif (
            values["stoch_k"]
            < values["stoch_d"]
        ):

            put_confirmations.append(
                "Stochastic bearish"
            )

        # =========================
        # ADX + DI
        # =========================

        if (
            values["adx"] >= 20
            and values["plus_di"]
            > values["minus_di"]
        ):

            call_confirmations.append(
                "ADX bullish"
            )

        elif (
            values["adx"] >= 20
            and values["minus_di"]
            > values["plus_di"]
        ):

            put_confirmations.append(
                "ADX bearish"
            )

        call_count = len(
            call_confirmations
        )

        put_count = len(
            put_confirmations
        )

        # =========================
        # SIGNAL DECISION
        # =========================

        if values["adx"] < 20:

            signal = "NO SIGNAL"
            confidence = 0

        elif (
            call_count >= 4
            and call_count > put_count
        ):

            signal = "CALL"

            confidence = round(
                (call_count / 6) * 100,
                1
            )

        elif (
            put_count >= 4
            and put_count > call_count
        ):

            signal = "PUT"

            confidence = round(
                (put_count / 6) * 100,
                1
            )

        else:

            signal = "NO SIGNAL"
            confidence = 0

        return {
            "pair": pair,
            "price": price,
            "signal": signal,
            "confidence": confidence,
            "call_count": call_count,
            "put_count": put_count,
            "call_confirmations":
                call_confirmations,
            "put_confirmations":
                put_confirmations,
            "values": values,
            "candle_time":
                str(candle["timestamp"]),
        }, None

    except Exception as e:

        return None, str(e)


# =========================
# AI VALIDATION
# =========================

def validate_with_ai(result):

    if not AIRFORCE_API_KEY:

        return None, (
            "AIRFORCE_API_KEY missing"
        )

    if result["signal"] == "NO SIGNAL":

        return {
            "decision": "REJECT",
            "direction": "NO SIGNAL",
            "confidence": 0,
            "reason":
                "Indicator engine produced NO SIGNAL."
        }, None

    if result["values"]["adx"] < 20:

        return {
            "decision": "REJECT",
            "direction": "NO SIGNAL",
            "confidence": 0,
            "reason": "ADX below 20."
        }, None

    values = result["values"]

    system_prompt = """
You are a strict trading signal validator.

You do NOT create a new signal.

You ONLY validate the proposed indicator direction.

Return ONLY valid JSON.

Required format:

{
  "decision": "APPROVE" or "REJECT",
  "direction": "CALL" or "PUT" or "NO SIGNAL",
  "confidence": integer from 0 to 100,
  "reason": "short reason"
}

Rules:

1. Direction must match the proposed signal
   to approve.

2. Reject weak or contradictory evidence.

3. Approve only when the indicator evidence
   reasonably supports the proposed direction.

4. Do not invent market data.

5. Confidence is a validation score,
   NOT a win probability.

6. If evidence is insufficient, REJECT.

7. Be conservative.
"""

    user_prompt = f"""
Proposed signal: {result["signal"]}

Indicator score: {result["confidence"]}

CALL confirmations:
{result["call_confirmations"]}

PUT confirmations:
{result["put_confirmations"]}

EMA9: {values["ema9"]}
EMA21: {values["ema21"]}
EMA50: {values["ema50"]}
EMA200: {values["ema200"]}

RSI: {values["rsi"]}

MACD: {values["macd"]}
MACD Signal: {values["macd_signal"]}
MACD Histogram: {values["macd_histogram"]}

BB Upper: {values["bb_upper"]}
BB Middle: {values["bb_middle"]}
BB Lower: {values["bb_lower"]}

Stochastic K: {values["stoch_k"]}
Stochastic D: {values["stoch_d"]}

ADX: {values["adx"]}
+DI: {values["plus_di"]}
-DI: {values["minus_di"]}

Validate this proposed signal.
"""

    headers = {
        "Authorization":
            f"Bearer {AIRFORCE_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": AIRFORCE_MODEL,
        "temperature": 0,
        "max_tokens": 200,
        "messages": [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ]
    }

    try:

        response = requests.post(
            AIRFORCE_URL,
            headers=headers,
            json=payload,
            timeout=25
        )

        if response.status_code == 429:

            retry_after = response.headers.get(
                "Retry-After",
                "unknown"
            )

            return None, (
                "Airforce rate limit (429). "
                f"Retry-After: {retry_after}"
            )

        response.raise_for_status()

        data = response.json()

        choices = data.get(
            "choices",
            []
        )

        if not choices:

            return None, (
                "Airforce returned no choices"
            )

        message = choices[0].get(
            "message",
            {}
        )

        content = message.get(
            "content"
        )

        if not content:

            return None, (
                "Airforce returned empty response"
            )

        content = str(content).strip()

        # Remove markdown JSON fences
        if content.startswith("```"):

            content = content.replace(
                "```json",
                ""
            ).replace(
                "```",
                ""
            ).strip()

        ai_result = json.loads(content)

        decision = str(
            ai_result.get(
                "decision",
                ""
            )
        ).upper()

        direction = str(
            ai_result.get(
                "direction",
                ""
            )
        ).upper()

        try:
            confidence = int(
                ai_result.get(
                    "confidence",
                    0
                )
            )
        except Exception:
            confidence = 0

        reason = str(
            ai_result.get(
                "reason",
                ""
            )
        )

        if decision not in [
            "APPROVE",
            "REJECT"
        ]:

            return None, (
                "AI returned invalid decision"
            )

        if direction not in [
            "CALL",
            "PUT",
            "NO SIGNAL"
        ]:

            return None, (
                "AI returned invalid direction"
            )

        if confidence < 0 or confidence > 100:

            return None, (
                "AI returned invalid confidence"
            )

        return {
            "decision": decision,
            "direction": direction,
            "confidence": confidence,
            "reason": reason,
        }, None

    except requests.exceptions.Timeout:

        return None, (
            "Airforce request timed out"
        )

    except requests.exceptions.RequestException as e:

        return None, str(e)

    except json.JSONDecodeError:

        return None, (
            "AI response was not valid JSON"
        )

    except Exception as e:

        return None, str(e)


# =========================
# /START
# =========================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🤖 Priyanithan AI Indicator Bot\n\n"
        "📊 Indicator engine: READY\n"
        "🤖 AI validation: ENABLED\n"
        "📡 AI Provider: Api.Airforce\n"
        "⚡ Auto-trade: OFF\n"
        "🛑 Martingale: OFF\n\n"
        "Use:\n"
        "/access YOUR_CODE\n"
        "/status\n"
        "/aitest\n"
        "/signal EURUSD\n"
        "/signal GBPUSD"
    )


# =========================
# /ACCESS
# =========================

async def access_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if not context.args:

        await update.message.reply_text(
            "❌ Access code missing.\n\n"
            "Use:\n"
            "/access YOUR_CODE"
        )

        return

    code = context.args[0]

    if ACCESS_CODE and code == ACCESS_CODE:

        authorized_users.add(user_id)

        await update.message.reply_text(
            "✅ Access authorized."
        )

    else:

        await update.message.reply_text(
            "❌ Invalid access code."
        )


# =========================
# /STATUS
# =========================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if user_id not in authorized_users:

        await update.message.reply_text(
            "🔒 Access required.\n\n"
            "Use /access YOUR_CODE"
        )

        return

    if AIRFORCE_API_KEY:

        ai_status = "READY"

    else:

        ai_status = "NOT CONFIGURED"

    await update.message.reply_text(
        "🟢 Bot Status: ONLINE\n\n"
        "🔐 Access: AUTHORIZED\n\n"
        "📡 Market API: Yahoo Finance\n\n"
        "📊 Indicator Engine: READY\n\n"
        "📈 EMA 9/21/50/200: READY\n"
        "📊 RSI 14: READY\n"
        "📉 MACD: READY\n"
        "〰️ Bollinger Bands: READY\n"
        "📐 Stochastic: READY\n"
        "💪 ADX: READY\n\n"
        f"🤖 AI Validator: {ai_status}\n"
        f"🧠 AI Provider: Api.Airforce\n"
        f"🧠 AI Model: {AIRFORCE_MODEL}\n\n"
        "⚡ Auto-trade: OFF\n"
        "🛑 Martingale: OFF"
    )


# =========================
# /AITEST
# =========================

async def aitest_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if user_id not in authorized_users:

        await update.message.reply_text(
            "🔒 Access required.\n\n"
            "Use /access YOUR_CODE"
        )

        return

    if not AIRFORCE_API_KEY:

        await update.message.reply_text(
            "❌ AI API key is missing.\n\n"
            "Check Render Environment Variables:\n"
            "AIRFORCE_API_KEY"
        )

        return

    await update.message.reply_text(
        "🤖 Testing Airforce AI connection..."
    )

    headers = {
        "Authorization":
            f"Bearer {AIRFORCE_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": AIRFORCE_MODEL,
        "temperature": 0,
        "max_tokens": 50,
        "messages": [
            {
                "role": "system",
                "content":
                    "Reply with exactly: AI READY"
            },
            {
                "role": "user",
                "content":
                    "Test the AI connection."
            }
        ]
    }

    try:

        response = requests.post(
            AIRFORCE_URL,
            headers=headers,
            json=payload,
            timeout=20
        )

        if response.status_code == 429:

            retry_after = response.headers.get(
                "Retry-After",
                "unknown"
            )

            await update.message.reply_text(
                "❌ AI CONNECTION FAILED\n\n"
                "Reason: Airforce rate limit (429)\n"
                f"Retry-After: {retry_after}\n\n"
                "No signal will be generated "
                "while AI is unavailable."
            )

            return

        response.raise_for_status()

        data = response.json()

        choices = data.get(
            "choices",
            []
        )

        if not choices:

            raise RuntimeError(
                "No choices returned"
            )

        reply = str(
            choices[0]["message"]["content"]
        ).strip()

        await update.message.reply_text(
            "🤖 AI TEST\n\n"
            "🟢 Api.Airforce: CONNECTED\n"
            f"🧠 Model: {AIRFORCE_MODEL}\n"
            f"💬 Response: {reply}"
        )

    except requests.exceptions.Timeout:

        await update.message.reply_text(
            "❌ AI CONNECTION FAILED\n\n"
            "Reason: Airforce request timed out."
        )

    except requests.exceptions.RequestException as e:

        await update.message.reply_text(
            "❌ AI CONNECTION FAILED\n\n"
            f"Reason: {str(e)}"
        )

    except Exception as e:

        await update.message.reply_text(
            "❌ AI CONNECTION FAILED\n\n"
            f"Reason: {str(e)}"
        )


# =========================
# FORMAT SIGNAL
# =========================

def format_signal(
    result,
    ai_result=None
):

    values = result["values"]

    message = (
        "📊 INDICATOR ANALYSIS\n\n"

        f"💱 Market: {result['pair']}\n"
        f"💰 Price: {result['price']:.5f}\n"
        "⏱ Timeframe: 1 minute\n"
        f"🕐 Closed candle: "
        f"{result['candle_time']}\n\n"

        f"EMA9: {values['ema9']:.5f}\n"
        f"EMA21: {values['ema21']:.5f}\n"
        f"EMA50: {values['ema50']:.5f}\n"
        f"EMA200: {values['ema200']:.5f}\n\n"

        f"RSI: {values['rsi']:.2f}\n\n"

        f"MACD: {values['macd']:.5f}\n"
        f"Signal: "
        f"{values['macd_signal']:.5f}\n"
        f"Histogram: "
        f"{values['macd_histogram']:.5f}\n\n"

        f"BB Upper: "
        f"{values['bb_upper']:.5f}\n"
        f"BB Middle: "
        f"{values['bb_middle']:.5f}\n"
        f"BB Lower: "
        f"{values['bb_lower']:.5f}\n\n"

        f"Stoch K: "
        f"{values['stoch_k']:.2f}\n"
        f"Stoch D: "
        f"{values['stoch_d']:.2f}\n\n"

        f"ADX: {values['adx']:.2f}\n"
        f"+DI: {values['plus_di']:.2f}\n"
        f"-DI: {values['minus_di']:.2f}\n\n"

        f"🟢 CALL confirmations: "
        f"{result['call_count']}\n"
        f"🔴 PUT confirmations: "
        f"{result['put_count']}\n\n"

        f"🎯 INDICATOR SIGNAL: "
        f"{result['signal']}\n"
        f"📈 Indicator score: "
        f"{result['confidence']}%\n"
    )

    if result["call_confirmations"]:

        message += (
            "\n🟢 CALL:\n- "
            + "\n- ".join(
                result["call_confirmations"]
            )
        )

    if result["put_confirmations"]:

        message += (
            "\n\n🔴 PUT:\n- "
            + "\n- ".join(
                result["put_confirmations"]
            )
        )

    if ai_result:

        message += (
            "\n\n🤖 AI VALIDATION\n\n"
            f"Decision: "
            f"{ai_result['decision']}\n"
            f"Direction: "
            f"{ai_result['direction']}\n"
            f"AI validation score: "
            f"{ai_result['confidence']}%\n"
            f"Reason: "
            f"{ai_result['reason']}\n"
        )

        if (
            ai_result["decision"]
            == "APPROVE"
            and ai_result["direction"]
            == result["signal"]
            and ai_result["confidence"] >= 89
        ):

            message += (
                "\n🎯 FINAL SIGNAL: "
                f"{result['signal']}\n"
            )

        else:

            message += (
                "\n🎯 FINAL SIGNAL: "
                "NO SIGNAL\n"
            )

    message += (
        "\n\n⚠️ Data source: Yahoo Finance\n"
        "⚠️ This is not an Olymp Trade price feed.\n"
        "⚠️ Indicator/AI scores are not "
        "guaranteed win probabilities.\n"
        "⚡ Manual execution only."
    )

    return message


# =========================
# /SIGNAL
# =========================

async def signal_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if user_id not in authorized_users:

        await update.message.reply_text(
            "🔒 Access required.\n\n"
            "Use /access YOUR_CODE"
        )

        return

    if not context.args:

        await update.message.reply_text(
            "❌ Pair missing.\n\n"
            "Use:\n"
            "/signal EURUSD\n"
            "/signal GBPUSD"
        )

        return

    pair = context.args[0].upper()

    if pair not in PAIRS:

        await update.message.reply_text(
            "❌ Unsupported pair.\n\n"
            "Available:\n"
            "EURUSD\n"
            "GBPUSD"
        )

        return

    await update.message.reply_text(
        f"⏳ Analyzing {pair}..."
    )

    # =========================
    # MARKET DATA
    # =========================

    data, error = get_fx_data(pair)

    if error:

        await update.message.reply_text(
            "❌ LIVE MARKET DATA FAILED\n\n"
            f"Market: {pair}\n\n"
            f"Reason: {error}\n\n"
            "No signal generated."
        )

        return

    # =========================
    # INDICATOR ANALYSIS
    # =========================

    result, error = analyze_indicators(
        data,
        pair
    )

    if error:

        await update.message.reply_text(
            "❌ Indicator analysis failed.\n\n"
            f"Reason: {error}\n\n"
            "No signal generated."
        )

        return

    # =========================
    # NO SIGNAL
    # =========================

    if result["signal"] == "NO SIGNAL":

        await update.message.reply_text(
            format_signal(result)
        )

        return

    # =========================
    # AI VALIDATION
    # =========================

    await update.message.reply_text(
        "🤖 Indicator signal found.\n"
        "⏳ Airforce AI validation running..."
    )

    ai_result, ai_error = validate_with_ai(
        result
    )

    if ai_error:

        await update.message.reply_text(
            "❌ AI VALIDATION FAILED\n\n"
            f"Reason: {ai_error}\n\n"
            "🎯 FINAL SIGNAL: NO SIGNAL\n\n"
            "No signal generated because "
            "AI validation was unavailable."
        )

        return

    await update.message.reply_text(
        format_signal(
            result,
            ai_result
        )
    )


# =========================
# ERROR HANDLER
# =========================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    print(
        "Telegram error:",
        context.error
    )


# =========================
# FLASK THREAD
# =========================

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


# =========================
# MAIN
# =========================

def main():

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing"
        )

    if not ACCESS_CODE:

        raise RuntimeError(
            "ACCESS_CODE is missing"
        )

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    application = (
        Application.builder()
        .token(
            TELEGRAM_BOT_TOKEN
        )
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        CommandHandler(
            "access",
            access_command
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status_command
        )
    )

    application.add_handler(
        CommandHandler(
            "aitest",
            aitest_command
        )
    )

    application.add_handler(
        CommandHandler(
            "signal",
            signal_command
        )
    )

    application.add_error_handler(
        error_handler
    )

    print(
        "🤖 Telegram polling started..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
````

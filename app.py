import asyncio
import io
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands

from olymptrade_ws import OlympTradeClient
from olymptrade_ws.olympconfig import parameters

# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ACCESS_CODE = os.getenv("ACCESS_CODE")

# Never put secrets in this file. Set them as Render Environment Variables.
OLYMPTRADE_ACCESS_TOKEN = os.getenv("OLYMPTRADE_ACCESS_TOKEN")

# AI: OpenRouter is preferred when configured; Airforce remains supported.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "z-ai/glm-5.2:free")
AIRFORCE_API_KEY = os.getenv("AIRFORCE_API_KEY")
AIRFORCE_MODEL = os.getenv("AIRFORCE_MODEL", "gpt-oss-120b")
AI_MIN_CONFIDENCE = int(os.getenv("AI_MIN_CONFIDENCE", "89"))

# Signal scan interval: scans periodically, but does NOT force a signal.
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))

# Live monitoring interval requested by the user.
LIVE_UPDATE_SECONDS = 15

# Manual execution only. This project deliberately contains no order placement call.
AUTO_TRADE = False
MARTINGALE = False

# Exact OlympTrade symbols can be changed through Render env if needed.
PAIR_ENV = os.getenv("OLYMP_PAIRS", "ASIA_X")
PAIRS = [x.strip().upper() for x in PAIR_ENV.split(",") if x.strip()]

# Optional mapping if the broker uses a different exact symbol.
PAIR_ALIASES = {
    "ASIA_X": os.getenv("OT_ASIA_X_PAIR", "ASIA_X"),
    "EURUSD": os.getenv("OT_EURUSD_PAIR", "EURUSD"),
    "GBPUSD": os.getenv("OT_GBPUSD_PAIR", "GBPUSD"),
}

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("priyanithan")

APP_VERSION = "2.2-asia-x-live-tick-candles"
app = Flask(__name__)
authorized_users = set()

# Global async/runtime state
ot_client = None
runtime_loop = None
latest_candles = {}
latest_ticks = {}
latest_signal = {}

# Live tick -> 1-minute OHLC fallback buffers.
# Used only when OlympTrade historical get_candles() returns no usable candles.
tick_history = {}
live_candles = {}

manual_trades = {}
state_lock = threading.Lock()

# ============================================================
# WEB HEALTH
# ============================================================

@app.get("/")
def home():
    return f"Priyanithan AI OlympTrade Signal Bot is ONLINE — {APP_VERSION}"

@app.get("/health")
def health():
    return "OK"

# ============================================================
# HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)

def is_authorized(update: Update):
    return update.effective_user and update.effective_user.id in authorized_users

def normalize_candles(raw):
    """Accept the library's documented candle list and normalize field names."""
    if not isinstance(raw, list):
        return None
    rows = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        # The supplied API reference documents open/high/low/close;
        # support common short names too.
        o = c.get("open", c.get("o"))
        h = c.get("high", c.get("h"))
        l = c.get("low", c.get("l"))
        cl = c.get("close", c.get("c"))
        ts = c.get("timestamp", c.get("t", c.get("time")))
        if o is None or h is None or l is None or cl is None:
            continue
        try:
            rows.append({
                "timestamp": float(ts) if ts is not None else time.time(),
                "open": float(o), "high": float(h),
                "low": float(l), "close": float(cl),
                "volume": float(c.get("volume", c.get("v", 0)) or 0),
            })
        except (TypeError, ValueError):
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows)
    return df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

# ============================================================
# OLYMP TRADE CONNECTION
# ============================================================

async def on_tick(message):
    data = message.get("d", [])
    if not isinstance(data, list):
        return

    for tick in data:
        if not isinstance(tick, dict):
            continue

        pair = tick.get("p", tick.get("pair"))
        price = tick.get("q", tick.get("price"))
        ts = tick.get("t", tick.get("timestamp", time.time()))

        if not pair or price is None:
            continue

        try:
            pair = str(pair).upper()
            price = float(price)
            ts = float(ts)
            minute = int(ts // 60) * 60

            with state_lock:
                latest_ticks[pair] = {
                    "price": price,
                    "timestamp": ts,
                }

                hist = tick_history.setdefault(pair, {})
                candle = hist.get(minute)

                if candle is None:
                    hist[minute] = {
                        "timestamp": float(minute),
                        "open": price,
                        "high": price,
                        "low": price,
                        "close": price,
                        "volume": 0.0,
                    }
                else:
                    candle["high"] = max(candle["high"], price)
                    candle["low"] = min(candle["low"], price)
                    candle["close"] = price
                    candle["volume"] += 1.0

                # Keep the current minute plus a generous rolling history.
                if len(hist) > 400:
                    for old_minute in sorted(hist)[:-400]:
                        hist.pop(old_minute, None)

                # Only completed minutes are used for technical analysis.
                completed = [
                    v for k, v in sorted(hist.items())
                    if k < minute
                ]
                live_candles[pair] = completed[-300:]

        except (TypeError, ValueError):
            continue

async def on_balance(message):
    # We keep balance read-only; no order placement is performed.
    log.debug("OlympTrade balance update received.")

async def on_trade_event(message):
    """Observe platform trade events only; never create/modify a trade."""
    event = message.get("e")
    data = message.get("d", [])
    if not isinstance(data, list):
        return
    for item in data:
        if not isinstance(item, dict):
            continue
        tid = item.get("id")
        if not tid:
            continue
        with state_lock:
            rec = manual_trades.setdefault(str(tid), {})
            rec["event"] = event
            rec["data"] = item
            rec["updated_at"] = time.time()

async def olymptrade_connect_loop():
    """Maintain a resilient read-only OlympTrade WebSocket connection.

    The supplied OlympTrade client intentionally stops its internal processing
    tasks when the socket is lost. Render can also interrupt long-lived
    WebSocket connections during deploys, maintenance, or network events.
    Therefore the application owns the reconnect loop here.

    No trade/order method is called by this loop.
    """
    global ot_client
    if not OLYMPTRADE_ACCESS_TOKEN:
        log.error("OLYMPTRADE_ACCESS_TOKEN is not configured.")
        return

    retry_delay = 5
    max_retry_delay = 60
    attempt = 0

    while True:
        client = None
        try:
            attempt += 1
            log.info("OlympTrade connection attempt #%s", attempt)

            client = OlympTradeClient(
                access_token=OLYMPTRADE_ACCESS_TOKEN,
                log_raw_messages=False,
            )
            client.register_callback(parameters.E_TICK_UPDATE, on_tick)
            client.register_callback(parameters.E_BALANCE_UPDATE, on_balance)
            client.register_callback(parameters.E_TRADE_ACCEPTED, on_trade_event)
            client.register_callback(parameters.E_TRADE_UPDATE_INTERIM, on_trade_event)
            client.register_callback(parameters.E_TRADE_CLOSED, on_trade_event)

            await client.start()
            ot_client = client

            # Initialize/read the session. This is read-only.
            await client.balance.get_balance()

            # Re-subscribe after EVERY successful reconnect.
            for pair in PAIRS:
                broker_pair = PAIR_ALIASES.get(pair, pair)
                try:
                    await client.market.subscribe_ticks(broker_pair)
                    log.info("Subscribed to OlympTrade ticks: %s", broker_pair)
                except Exception as e:
                    log.warning("Tick subscription failed for %s: %s", broker_pair, e)

            log.info("OlympTrade connection established for: %s", PAIRS)

            # Connection is healthy; reset backoff.
            retry_delay = 5
            attempt = 0

            # Keep this connection alive. The library's ping loop handles
            # keep-alive; this loop detects when that connection is gone.
            while client.connection.is_connected:
                await asyncio.sleep(5)

            raise ConnectionError("OlympTrade WebSocket disconnected")

        except asyncio.CancelledError:
            # Application shutdown/redeploy. Do not keep reconnecting.
            log.info("OlympTrade connection loop cancelled.")
            raise
        except Exception as e:
            log.error("OlympTrade connection error: %s", e)
            ot_client = None

            # The library's connection-lost handler already marks the client
            # as stopped. Avoid calling client.stop() again, which only emits
            # a misleading 'Client is not running' warning.
            try:
                if client and client.connection.is_connected:
                    await client.connection.disconnect()
            except Exception:
                pass

            log.warning(
                "OlympTrade reconnecting in %s seconds (exponential backoff).",
                retry_delay,
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)

async def get_ot_candles(pair, size=60, count=260):
    """Fetch OlympTrade candles; fall back to locally aggregated live ticks."""
    client = ot_client

    # First try the broker's historical candle endpoint.
    if client is not None and client.connection.is_connected:
        broker_pair = PAIR_ALIASES.get(pair, pair)
        try:
            raw = await client.market.get_candles(
                broker_pair,
                size=size,
                count=count
            )
            df = normalize_candles(raw)
            if df is not None and len(df) >= 220:
                return df, None
        except Exception as e:
            log.warning("Historical candle request failed for %s: %s", pair, e)

    # Fallback: build true 1-minute OHLC candles from OlympTrade live ticks.
    # We never fabricate missing prices. Until enough completed candles exist,
    # the bot must remain in NO SIGNAL state.
    with state_lock:
        rows = list(live_candles.get(pair, []))

    if len(rows) < 220:
        return None, (
            f"Not enough live 1-minute candles ({len(rows)}/220). "
            "Collecting OlympTrade live tick history; no signal generated."
        )

    df = pd.DataFrame(rows[-count:])
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    if len(df) < 220:
        return None, f"Not enough live 1-minute candles ({len(df)}/220)"

    return df, None

# ============================================================
# TECHNICAL / PRICE ACTION ENGINE
# ============================================================

def candle_patterns(df):
    r = {}
    a = df.iloc[-2]  # last closed candle
    b = df.iloc[-3]
    body = abs(a.close - a.open)
    rng = max(a.high - a.low, 1e-12)
    upper = a.high - max(a.open, a.close)
    lower = min(a.open, a.close) - a.low
    bull = a.close > a.open
    bear = a.close < a.open

    r["Doji"] = body <= rng * 0.10
    r["Hammer"] = lower >= body * 2 and upper <= max(body, rng * 0.15)
    r["Inverted Hammer"] = upper >= body * 2 and lower <= max(body, rng * 0.15)
    r["Shooting Star"] = upper >= body * 2 and lower <= max(body, rng * 0.15) and bear
    r["Bullish Engulfing"] = bull and b.close < b.open and a.open <= b.close and a.close >= b.open
    r["Bearish Engulfing"] = bear and b.close > b.open and a.open >= b.close and a.close <= b.open
    r["Harami"] = max(a.open, a.close) <= max(b.open, b.close) and min(a.open, a.close) >= min(b.open, b.close)
    r["Pin Bar"] = max(upper, lower) >= rng * 0.60
    r["Marubozu"] = body >= rng * 0.80
    r["Spinning Top"] = body <= rng * 0.35 and upper >= rng * 0.20 and lower >= rng * 0.20

    # Three-candle patterns
    c = df.iloc[-4]
    r["Morning Star"] = (
        c.close < c.open and
        abs(b.close-b.open) <= abs(c.close-c.open)*0.45 and
        bull and a.close > (c.open+c.close)/2
    )
    r["Evening Star"] = (
        c.close > c.open and
        abs(b.close-b.open) <= abs(c.close-c.open)*0.45 and
        bear and a.close < (c.open+c.close)/2
    )

    # Tweezers / soldiers / crows
    r["Tweezer Bottom"] = abs(a.low-b.low) <= rng*0.08 and bull and b.close < b.open
    r["Tweezer Top"] = abs(a.high-b.high) <= rng*0.08 and bear and b.close > b.open

    last3 = df.iloc[-4:-1]
    r["Three White Soldiers"] = all(x.close > x.open for _, x in last3.iterrows())
    r["Three Black Crows"] = all(x.close < x.open for _, x in last3.iterrows())

    prev = df.iloc[-3]
    r["Inside Bar"] = a.high <= prev.high and a.low >= prev.low

    return [k for k, v in r.items() if v]

def structure(df):
    highs = df["high"].tail(30).to_numpy()
    lows = df["low"].tail(30).to_numpy()
    hh = highs[-1] > highs[-6] if len(highs) >= 6 else False
    hl = lows[-1] > lows[-6] if len(lows) >= 6 else False
    lh = highs[-1] < highs[-6] if len(highs) >= 6 else False
    ll = lows[-1] < lows[-6] if len(lows) >= 6 else False
    if hh and hl:
        trend = "Bullish"
    elif lh and ll:
        trend = "Bearish"
    else:
        trend = "Range / Mixed"
    return trend

def analyze(df, pair):
    x = df.copy()
    close = x["close"]
    high = x["high"]
    low = x["low"]

    x["ema9"] = EMAIndicator(close, 9).ema_indicator()
    x["ema21"] = EMAIndicator(close, 21).ema_indicator()
    x["ema50"] = EMAIndicator(close, 50).ema_indicator()
    x["ema200"] = EMAIndicator(close, 200).ema_indicator()

    macd = MACD(close, 26, 12, 9)
    x["macd"] = macd.macd()
    x["macd_signal"] = macd.macd_signal()
    x["macd_hist"] = macd.macd_diff()

    x["rsi"] = RSIIndicator(close, 14).rsi()

    bb = BollingerBands(close, 20, 2)
    x["bb_high"] = bb.bollinger_hband()
    x["bb_mid"] = bb.bollinger_mavg()
    x["bb_low"] = bb.bollinger_lband()

    st = StochasticOscillator(high, low, close, 14, 3)
    x["stoch_k"] = st.stoch()
    x["stoch_d"] = st.stoch_signal()

    adx = ADXIndicator(high, low, close, 14)
    x["adx"] = adx.adx()

    row = x.iloc[-2]  # closed candle
    price = float(row.close)
    pats = candle_patterns(x)
    trend = structure(x)

    bull = 0
    bear = 0
    reasons = []
    conflicts = []

    if row.ema9 > row.ema21:
        bull += 1; reasons.append("EMA 9/21 Bullish")
    elif row.ema9 < row.ema21:
        bear += 1; reasons.append("EMA 9/21 Bearish")

    if row.macd > row.macd_signal and row.macd_hist > 0:
        bull += 1; reasons.append("MACD Bullish")
    elif row.macd < row.macd_signal and row.macd_hist < 0:
        bear += 1; reasons.append("MACD Bearish")

    if 50 < row.rsi < 70:
        bull += 1; reasons.append("RSI Momentum")
    elif 30 < row.rsi < 50:
        bear += 1; reasons.append("RSI Momentum")
    elif row.rsi >= 70:
        conflicts.append("RSI Overbought")
    elif row.rsi <= 30:
        conflicts.append("RSI Oversold")

    if price > row.bb_mid:
        bull += 1; reasons.append("Above Bollinger Mid")
    elif price < row.bb_mid:
        bear += 1; reasons.append("Below Bollinger Mid")

    if row.stoch_k > row.stoch_d and row.stoch_k < 85:
        bull += 1; reasons.append("Stochastic Bullish")
    elif row.stoch_k < row.stoch_d and row.stoch_k > 15:
        bear += 1; reasons.append("Stochastic Bearish")

    if trend == "Bullish":
        bull += 1; reasons.append("Bullish Structure")
    elif trend == "Bearish":
        bear += 1; reasons.append("Bearish Structure")

    if "Bullish Engulfing" in pats or "Morning Star" in pats or "Three White Soldiers" in pats:
        bull += 1; reasons.append("Bullish Candle Pattern")
    if "Bearish Engulfing" in pats or "Evening Star" in pats or "Three Black Crows" in pats:
        bear += 1; reasons.append("Bearish Candle Pattern")

    adx_val = float(row.adx)
    if adx_val < 20:
        signal = "NO SIGNAL"
        reason = f"ADX is below 20 ({adx_val:.1f}); market strength is insufficient."
    elif bull == bear or max(bull, bear) < 4:
        signal = "NO SIGNAL"
        reason = "Bullish/bearish evidence is not strong enough or is conflicting."
    else:
        signal = "UP" if bull > bear else "DOWN"
        reason = "Strong multi-factor setup; pending AI validation."

    # Nearby resistance/support approximation from recent closed candles.
    recent = x.iloc[-22:-2]
    resistance = float(recent.high.max())
    support = float(recent.low.min())
    if signal == "UP" and resistance > price and (resistance-price)/max(price,1e-12) < 0.0008:
        conflicts.append("Resistance very close")
    if signal == "DOWN" and support < price and (price-support)/max(price,1e-12) < 0.0008:
        conflicts.append("Support very close")

    raw_score = max(bull, bear)
    confidence = min(99, int(55 + raw_score * 5 - len(conflicts) * 7))

    return {
        "pair": pair,
        "signal": signal,
        "price": price,
        "candle_time": datetime.fromtimestamp(float(row.timestamp), tz=timezone.utc).strftime("%H:%M:%S UTC"),
        "bull": bull, "bear": bear,
        "trend": trend,
        "adx": adx_val,
        "rsi": float(row.rsi),
        "macd": float(row.macd),
        "macd_signal": float(row.macd_signal),
        "stoch_k": float(row.stoch_k),
        "stoch_d": float(row.stoch_d),
        "patterns": pats,
        "reasons": reasons,
        "conflicts": conflicts,
        "support": support,
        "resistance": resistance,
        "confidence": confidence,
        "data_source": "OlympTrade live candle feed",
    }

# ============================================================
# AI VALIDATION
# ============================================================

def ai_prompt(result):
    return f"""
You are a cautious market-setup validator. Do NOT invent missing data.
Evaluate this OlympTrade live-market setup using price action, candle patterns,
market structure, indicators, support/resistance and volatility/ADX context.

Rules:
- ADX below 20 => NO SIGNAL.
- Conflicting evidence => NO SIGNAL.
- One candle pattern alone is never sufficient.
- APPROVE only when the setup is coherent and sufficiently strong.
- This is a validation score, NOT a guaranteed win probability.
- Return JSON only:
{{"decision":"APPROVE|REJECT","direction":"UP|DOWN|NO SIGNAL","confidence":0-100,"reason":"short reason"}}

DATA:
{json.dumps(result, ensure_ascii=False)}
""".strip()

def call_ai(prompt):
    """Call OpenRouter with GLM primary + free-router fallback."""
    if OPENROUTER_API_KEY:
        try:
            key = OPENROUTER_API_KEY.strip()
            primary_model = OPENROUTER_MODEL.strip() or "z-ai/glm-5.2:free"
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://priyanithan-ai.onrender.com",
                "X-Title": "Priyanithan AI OlympTrade Signal Bot",
            }
            payload = {
                "model": primary_model,
                "models": ["openrouter/free"],
                "messages": [
                    {"role": "system", "content": "Return JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": 300,
            }
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"].strip()
            content = re.sub(r"^```json\s*|\s*```$", "", content, flags=re.I)
            parsed = json.loads(content)
            served_model = data.get("model", primary_model)
            log.info("AI validation succeeded via OpenRouter model: %s", served_model)
            return parsed, None
        except Exception as e:
            last_error = f"OpenRouter: {e}"
            log.warning("OpenRouter primary/fallback failed: %s", last_error)
            # Airforce remains an optional secondary provider if configured.
            if AIRFORCE_API_KEY:
                try:
                    headers = {
                        "Authorization": f"Bearer {AIRFORCE_API_KEY.strip()}",
                        "Content-Type": "application/json",
                    }
                    payload = {
                        "model": AIRFORCE_MODEL,
                        "messages": [
                            {"role": "system", "content": "Return JSON only."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0,
                        "max_tokens": 300,
                    }
                    r = requests.post(
                        "https://api.airforce/v1/chat/completions",
                        headers=headers, json=payload, timeout=25
                    )
                    r.raise_for_status()
                    data = r.json()
                    content = data["choices"][0]["message"]["content"].strip()
                    content = re.sub(r"^```json\s*|\s*```$", "", content, flags=re.I)
                    return json.loads(content), None
                except Exception as e:
                    return None, f"{last_error}; Airforce: {e}"
            return None, last_error

    if AIRFORCE_API_KEY:
        try:
            headers = {
                "Authorization": f"Bearer {AIRFORCE_API_KEY.strip()}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": AIRFORCE_MODEL,
                "messages": [
                    {"role": "system", "content": "Return JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": 300,
            }
            r = requests.post("https://api.airforce/v1/chat/completions", headers=headers, json=payload, timeout=25)
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"].strip()
            content = re.sub(r"^```json\s*|\s*```$", "", content, flags=re.I)
            return json.loads(content), None
        except Exception as e:
            return None, f"Airforce: {e}"

    return None, "No AI provider configured"

# ============================================================
# TELEGRAM FORMATTING
# ============================================================

def format_signal(result, ai=None):
    if result["signal"] == "NO SIGNAL" or not ai:
        details = "\n".join(f"• {x}" for x in result["reasons"][-5:]) or "• No strong confirmation"
        conflicts = "\n".join(f"• {x}" for x in result["conflicts"]) or "• Setup strength insufficient"
        return (
            "🚫 NO SIGNAL\n\n"
            f"📈 {result['pair']}\n"
            f"🕐 {result['candle_time']}\n\n"
            "🧠 MARKET ANALYSIS\n"
            f"🕯️ Patterns: {', '.join(result['patterns']) or 'None'}\n"
            f"📈 Trend: {result['trend']}\n"
            f"💪 ADX: {result['adx']:.1f}\n\n"
            f"✅ Confirmations:\n{details}\n\n"
            f"⚠️ Conflicts:\n{conflicts}\n\n"
            "🤖 AI Decision: NO SIGNAL\n"
            "⏳ Waiting for stronger setup..."
        )

    direction = ai.get("direction", "NO SIGNAL")
    decision = ai.get("decision", "REJECT")
    conf = int(ai.get("confidence", 0))
    if decision != "APPROVE" or direction not in ("UP", "DOWN") or conf < AI_MIN_CONFIDENCE:
        return (
            "🚫 NO SIGNAL\n\n"
            f"📈 {result['pair']}\n"
            f"🕐 {result['candle_time']}\n\n"
            f"🕯️ Candle: {', '.join(result['patterns']) or 'No major pattern'}\n"
            f"📈 Trend: {result['trend']}\n"
            f"📊 RSI: {result['rsi']:.1f}\n"
            f"💪 ADX: {result['adx']:.1f}\n"
            f"⚠️ {ai.get('reason', 'AI rejected the setup.')}\n\n"
            "🤖 AI Decision: NO SIGNAL\n"
            "⏳ Waiting for stronger setup..."
        )

    fire = "🔥🔥🔥" if conf >= 94 else "🔥🔥" if conf >= 91 else "🔥"
    arrow = "⬆️" if direction == "UP" else "⬇️"
    duration = "1 MIN" if result["adx"] < 25 else ("2 MIN" if result["adx"] < 35 else "5 MIN")
    confirmations = "\n".join(f"• {x}" for x in result["reasons"][-7:])
    return (
        f"📈 {result['pair']}\n"
        f"{arrow} TRADE {direction}\n"
        f"🕐 {result['candle_time']}\n"
        f"{fire}\n\n"
        f"{confirmations}\n\n"
        f"⏱ Duration: {duration}\n"
        f"🤖 AI: APPROVED\n"
        f"📊 Confidence: {conf}%\n\n"
        "⚠️ Manual execution only"
    )

# ============================================================
# LIVE CHART IMAGE
# ============================================================

def make_chart(df, result=None):
    view = df.tail(60).copy()
    fig, ax = plt.subplots(figsize=(10, 5))
    for i, (_, r) in enumerate(view.iterrows()):
        up = r.close >= r.open
        ax.plot([i, i], [r.low, r.high], linewidth=1)
        ax.plot([i, i], [r.open, r.close], linewidth=5)
    ax.set_title(
        f"OlympTrade Live Candles — {result['pair'] if result else ''}"
    )
    ax.set_xlabel("Recent 1-minute candles from OlympTrade live ticks")
    ax.set_ylabel("Price")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140)
    plt.close(fig)
    buf.seek(0)
    return buf

# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start_command(update, context):
    await update.message.reply_text(
        "🤖 Priyanithan AI OlympTrade Bot\n\n"
        "Use /access YOUR_CODE first.\n"
        "Then /status or /signal ASIA_X"
    )

async def access_command(update, context):
    if not context.args:
        await update.message.reply_text("Use /access YOUR_CODE")
        return
    if ACCESS_CODE and context.args[0] == ACCESS_CODE:
        authorized_users.add(update.effective_user.id)
        await update.message.reply_text("✅ Access authorized.")
    else:
        await update.message.reply_text("❌ Invalid access code.")

async def status_command(update, context):
    if not is_authorized(update):
        await update.message.reply_text("🔒 Access required. Use /access YOUR_CODE")
        return
    connected = bool(ot_client and ot_client.connection.is_connected)
    ai = "OpenRouter" if OPENROUTER_API_KEY else ("Airforce" if AIRFORCE_API_KEY else "NOT CONFIGURED")
    await update.message.reply_text(
        "🟢 BOT STATUS\n\n"
        f"📡 OlympTrade Live Feed: {'CONNECTED' if connected else 'DISCONNECTED'}\n"
        f"📊 Pairs: {', '.join(PAIRS)}\n"
        f"🤖 AI: {ai}\n"
        f"🧠 Model: {OPENROUTER_MODEL if OPENROUTER_API_KEY else AIRFORCE_MODEL}\n\n"
        "⚡ Auto-trade: OFF\n"
        "🛑 Martingale: OFF\n"
        "👤 Execution: MANUAL ONLY\n"
        "📡 Source: OlympTrade"
    )

async def signal_command(update, context):
    if not is_authorized(update):
        await update.message.reply_text("🔒 Access required. Use /access YOUR_CODE")
        return
    if not context.args:
        await update.message.reply_text("Use /signal ASIA_X")
        return
    pair = context.args[0].upper()
    if pair not in PAIRS:
        await update.message.reply_text(f"❌ Unsupported pair. Available: {', '.join(PAIRS)}")
        return

    msg = await update.message.reply_text(f"⏳ Reading OlympTrade live candles for {pair}...")
    df, err = await get_ot_candles(pair, 60, 260)
    if err:
        await msg.edit_text(f"❌ LIVE DATA FAILED\n\n{err}\n\nNo signal generated.")
        return

    result = analyze(df, pair)
    with state_lock:
        latest_candles[pair] = df
        latest_signal[pair] = result

    if result["signal"] == "NO SIGNAL":
        await msg.edit_text(format_signal(result))
        return

    await msg.edit_text("🤖 Strong setup found. AI validation running...")
    ai, ai_err = call_ai(ai_prompt(result))
    if ai_err:
        await msg.edit_text(
            "🚫 NO SIGNAL\n\n"
            f"📈 {pair}\n"
            f"❌ AI validation unavailable: {ai_err}\n\n"
            "No trade signal generated."
        )
        return

    await msg.edit_text(format_signal(result, ai))

# ============================================================
# BACKGROUND SCANNER
# ============================================================

async def scan_loop(application):
    await asyncio.sleep(20)
    while True:
        try:
            for pair in PAIRS:
                df, err = await get_ot_candles(pair, 60, 260)
                if err:
                    continue
                result = analyze(df, pair)
                with state_lock:
                    latest_candles[pair] = df
                    latest_signal[pair] = result

                # No forced signals. Only notify when the setup passes local filters
                # and AI also approves at the configured threshold.
                if result["signal"] == "NO SIGNAL":
                    continue

                ai, ai_err = call_ai(ai_prompt(result))
                if ai_err or not ai:
                    continue
                direction = ai.get("direction")
                conf = int(ai.get("confidence", 0))
                if ai.get("decision") == "APPROVE" and direction == result["signal"] and conf >= AI_MIN_CONFIDENCE:
                    text = format_signal(result, ai)
                    for uid in list(authorized_users):
                        try:
                            await application.bot.send_message(chat_id=uid, text=text)
                        except Exception as e:
                            log.warning("Telegram signal send failed: %s", e)
        except Exception:
            log.exception("Scanner loop error")
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)

# ============================================================
# MANUAL TRADE MONITOR
# ============================================================

async def manual_trade_monitor(application):
    """
    Observes open/manual trades when the supplied library exposes them.
    It never calls place_order/place_trade.
    """
    while True:
        try:
            client = ot_client
            if client and client.connection.is_connected:
                # We only read open trades. Account id is obtained from the client's
                # session initialization if available.
                account_id = getattr(client, "account_id", None)
                if account_id:
                    try:
                        trades = await client.trade.get_open_trades(account_id, group="real")
                        if isinstance(trades, list):
                            with state_lock:
                                for t in trades:
                                    if isinstance(t, dict) and t.get("id"):
                                        manual_trades.setdefault(str(t["id"]), {})["open"] = t
                    except Exception as e:
                        log.debug("Open-trade read unavailable: %s", e)
        except Exception:
            log.exception("Manual monitor error")
        await asyncio.sleep(LIVE_UPDATE_SECONDS)

# ============================================================
# MAIN
# ============================================================

def run_flask():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

async def telegram_runtime(application):
    """Start PTB first, then create our long-running tasks.

    PTB's post_init hook runs before Application is marked as running, so
    creating background tasks there can produce warnings and unreliable task
    lifecycle handling. We explicitly start the Application/Updater first.
    """
    global runtime_loop
    runtime_loop = asyncio.get_running_loop()

    await application.initialize()
    await application.start()

    tasks = [
        asyncio.create_task(olymptrade_connect_loop(), name="olymptrade-connect"),
        asyncio.create_task(scan_loop(application), name="signal-scan"),
        asyncio.create_task(manual_trade_monitor(application), name="trade-monitor"),
    ]

    try:
        await application.updater.start_polling(drop_pending_updates=True)
        log.info("Telegram polling started; background bot tasks are running.")
        await asyncio.Event().wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        if application.updater and application.updater.running:
            await application.updater.stop()
        if application.running:
            await application.stop()
        await application.shutdown()

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
    if not ACCESS_CODE:
        raise RuntimeError("ACCESS_CODE is missing")
    if not OLYMPTRADE_ACCESS_TOKEN:
        raise RuntimeError("OLYMPTRADE_ACCESS_TOKEN is missing")

    threading.Thread(target=run_flask, daemon=True).start()

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("access", access_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("signal", signal_command))

    asyncio.run(telegram_runtime(application))

if __name__ == "__main__":
    main()

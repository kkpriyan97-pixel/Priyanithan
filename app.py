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
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
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

APP_VERSION = "2.4-asia-x-openrouter-fallback"
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
    """Fetch real OlympTrade historical candles, with live-tick fallback.

    This function is deliberately verbose in logs so Render clearly shows:
    1) candle request started
    2) candle response returned
    3) how many OHLC rows were received
    """
    client = ot_client
    broker_pair = PAIR_ALIASES.get(pair, pair)

    if client is not None and client.connection.is_connected:
        log.info("🔍 CANDLE REQUEST: pair=%s broker_pair=%s size=%s count=%s",
                 pair, broker_pair, size, count)
        try:
            raw = await client.market.get_candles(
                broker_pair,
                size=size,
                count=count
            )
            raw_rows = len(raw) if isinstance(raw, list) else 0
            log.info("🔍 CANDLE RESPONSE: pair=%s raw_rows=%s type=%s",
                     pair, raw_rows, type(raw).__name__)

            df = normalize_candles(raw)
            normalized_rows = len(df) if df is not None else 0
            log.info("🕯️ CANDLE NORMALIZED: pair=%s rows=%s",
                     pair, normalized_rows)

            if df is not None and len(df) >= 220:
                log.info("✅ HISTORICAL CANDLES READY: %s rows for %s",
                         len(df), pair)
                return df, None

            log.warning("⚠️ Historical candles insufficient for %s: %s/220",
                        pair, normalized_rows)
        except Exception as e:
            log.exception("❌ Historical candle request failed for %s: %s",
                          pair, e)
    else:
        log.warning("⚠️ CANDLE REQUEST SKIPPED: OlympTrade client not connected for %s",
                    pair)

    # Fallback: build true 1-minute OHLC candles from OlympTrade live ticks.
    # We never fabricate missing prices. Until enough completed candles exist,
    # the bot must remain in NO SIGNAL state.
    with state_lock:
        rows = list(live_candles.get(pair, []))

    log.info("📦 LIVE-TICK CANDLE FALLBACK: pair=%s completed_rows=%s",
             pair, len(rows))

    if len(rows) < 220:
        return None, (
            f"Not enough live 1-minute candles ({len(rows)}/220). "
            "Historical candle feed unavailable; collecting OlympTrade live ticks."
        )

    df = pd.DataFrame(rows[-count:])
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    if len(df) < 220:
        return None, f"Not enough live 1-minute candles ({len(df)}/220)"

    log.info("✅ LIVE-TICK CANDLES READY: %s rows for %s", len(df), pair)
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
    # ADX is a caution/penalty, not an automatic blocker.
    # A coherent directional setup can still be sent to AI for validation
    # when ADX is between 15 and 20.
    strength = float(max(bull, bear))
    margin = float(abs(bull - bear))
    directional = "UP" if bull > bear else ("DOWN" if bear > bull else "NO SIGNAL")

    if directional == "NO SIGNAL" or strength < 3.0 or margin < 0.75:
        signal = "NO SIGNAL"
        reason = "Directional evidence is not strong/coherent enough."
    elif margin < 1.25 and len(conflicts) >= 2:
        signal = "NO SIGNAL"
        reason = "Too much conflicting evidence near the directional boundary."
    else:
        signal = directional
        reason = "Directional multi-factor setup; pending AI validation."

    if adx_val < 15:
        conflicts.append(f"Very weak ADX ({adx_val:.1f})")
    elif adx_val < 20:
        conflicts.append(f"Weak ADX ({adx_val:.1f})")

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
- ADX below 15 is strong caution; ADX 15-20 reduces confidence but is not an automatic rejection.
- Conflicting evidence => NO SIGNAL.
- One candle pattern alone is never sufficient.
- APPROVE only when the setup is coherent and sufficiently strong.
- This is a validation score, NOT a guaranteed win probability.
- Return JSON only:
{{"decision":"APPROVE|REJECT","direction":"UP|DOWN|NO SIGNAL","confidence":0-100,"reason":"short reason"}}

DATA:
{json.dumps(result, ensure_ascii=False)}
""".strip()

def _parse_ai_response(data):
    """Extract JSON from common OpenRouter/OpenAI-compatible response shapes."""
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"AI response missing choices: {str(data)[:500]}")
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content")

    # Some providers return content as a list of blocks.
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                txt = block.get("text") or block.get("content") or ""
                if txt:
                    parts.append(str(txt))
            elif isinstance(block, str):
                parts.append(block)
        content = "".join(parts)

    if not content:
        # A few OpenAI-compatible providers may place text elsewhere.
        content = message.get("reasoning") or choices[0].get("text")

    if not content:
        raise ValueError("AI response contained no text content")

    content = str(content).strip()
    content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.I)
    content = re.sub(r"\s*```$", "", content)

    # If the model adds surrounding text, extract the first JSON object.
    if not content.startswith("{"):
        m = re.search(r"\{.*\}", content, flags=re.S)
        if m:
            content = m.group(0)

    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("AI JSON result is not an object")
    return parsed


def call_ai(prompt):
    """Try OpenRouter with the configured model and free-router fallbacks.

    A failed model/provider must not permanently block the next 5-minute scan.
    No trade execution is performed here.
    """
    providers = []

    if OPENROUTER_API_KEY:
        # Primary configured model, then OpenRouter's free router as a fallback.
        models = []
        for model in (
            OPENROUTER_MODEL,
            "openrouter/free",
            "minimax/minimax-m3:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "google/gemma-4-26b-a4b-it:free",
        ):
            if model and model not in models:
                models.append(model)
        for model in models:
            providers.append((
                f"OpenRouter/{model}",
                "https://openrouter.ai/api/v1/chat/completions",
                OPENROUTER_API_KEY,
                model,
            ))

    if AIRFORCE_API_KEY:
        providers.append((
            "Airforce",
            "https://api.airforce/v1/chat/completions",
            AIRFORCE_API_KEY,
            AIRFORCE_MODEL,
        ))

    if not providers:
        return None, "No AI provider configured"

    last_error = None
    for name, url, key, model in providers:
        try:
            headers = {
                "Authorization": f"Bearer {key.strip()}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "Return JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": 300,
            }
            if name.startswith("OpenRouter/"):
                headers["HTTP-Referer"] = "https://priyanithan-ai.onrender.com"
                headers["X-Title"] = "Priyanithan AI OlympTrade Signal Bot"

            r = requests.post(url, headers=headers, json=payload, timeout=15)

            if not r.ok:
                # Keep the useful API body; it tells us whether the failure is
                # model, authentication, quota, routing, or endpoint related.
                body = r.text[:700].replace("\n", " ")
                raise RuntimeError(f"HTTP {r.status_code}: {body}")

            try:
                data = r.json()
            except Exception:
                body = r.text[:700].replace("\n", " ")
                raise RuntimeError(f"Non-JSON AI response: {body or '<empty response>'}")
            parsed = _parse_ai_response(data)
            log.info("🤖 AI VALIDATION OK: provider=%s model=%s", name, model)
            return parsed, None

        except Exception as e:
            last_error = f"{name}: {e}"
            log.warning("AI provider failed: %s", last_error)
            continue

    return None, last_error or "AI failed"

# ============================================================
# BACKGROUND SCANNER
# ============================================================

async def scan_loop(application):
    """
    Automatic 5-minute signal cycle.

    Sends a Telegram status message for every cycle:
      - APPROVED UP/DOWN -> trade signal
      - AI rejected -> NO SIGNAL + reason
      - data/AI unavailable -> diagnostic message

    Auto-trading remains OFF; this function only sends Telegram messages.
    """
    await asyncio.sleep(20)

    while True:
        cycle_started = time.time()
        try:
            for pair in PAIRS:
                recipients = list(authorized_users)
                if not recipients:
                    log.warning("AUTO SCAN: no authorized Telegram users; use /access YOUR_CODE")
                    continue

                df, err = await get_ot_candles(pair, 60, 260)

                if err:
                    msg = (
                        "⚠️ 5-MINUTE SCAN\n\n"
                        f"📈 {pair}\n"
                        "❌ LIVE DATA UNAVAILABLE\n\n"
                        f"{err}"
                    )
                    for uid in recipients:
                        try:
                            await application.bot.send_message(chat_id=uid, text=msg)
                        except Exception as e:
                            log.warning("Telegram scan status send failed: %s", e)
                    continue

                result = analyze(df, pair)
                with state_lock:
                    latest_candles[pair] = df
                    latest_signal[pair] = result

                if result["signal"] == "NO SIGNAL":
                    msg = (
                        "🚫 NO SIGNAL\n\n"
                        f"📈 {pair}\n"
                        f"🕐 {result.get('candle_time', 'N/A')}\n\n"
                        "📊 Technical analysis did not find a strong setup.\n"
                        f"📈 Trend: {result.get('trend', 'N/A')}\n"
                        f"📊 RSI: {result.get('rsi', 0):.1f}\n"
                        f"💪 ADX: {result.get('adx', 0):.1f}\n\n"
                        "⏳ Waiting for a stronger setup..."
                    )
                    for uid in recipients:
                        try:
                            await application.bot.send_message(chat_id=uid, text=msg)
                        except Exception as e:
                            log.warning("Telegram NO SIGNAL send failed: %s", e)
                    continue

                ai, ai_err = call_ai(ai_prompt(result))

                if ai_err or not ai:
                    msg = (
                        "⚠️ AI VALIDATION UNAVAILABLE\n\n"
                        f"📈 {pair}\n"
                        f"📊 Technical candidate: {result['signal']}\n"
                        f"📊 Technical confidence: {result.get('confidence', 0)}%\n\n"
                        f"❌ {ai_err or 'No AI response'}\n\n"
                        "🚫 No trade signal generated."
                    )
                    for uid in recipients:
                        try:
                            await application.bot.send_message(chat_id=uid, text=msg)
                        except Exception as e:
                            log.warning("Telegram AI status send failed: %s", e)
                    continue

                direction = str(ai.get("direction", "NO SIGNAL")).upper()
                decision = str(ai.get("decision", "REJECT")).upper()
                try:
                    conf = int(ai.get("confidence", 0))
                except (TypeError, ValueError):
                    conf = 0

                approved = (
                    decision == "APPROVE"
                    and direction in ("UP", "DOWN")
                    and direction == result["signal"]
                    and conf >= AI_MIN_CONFIDENCE
                )

                if approved:
                    msg = format_signal(result, ai)
                else:
                    reason = str(
                        ai.get("reason")
                        or ai.get("analysis")
                        or "AI rejected the setup."
                    )
                    msg = (
                        "🚫 NO SIGNAL\n\n"
                        f"📈 {pair}\n"
                        f"🕐 {result.get('candle_time', 'N/A')}\n\n"
                        f"📊 Technical candidate: {result['signal']}\n"
                        f"📊 Technical confidence: {result.get('confidence', 0)}%\n"
                        f"🤖 AI decision: {decision}\n"
                        f"🧭 AI direction: {direction}\n"
                        f"📊 AI confidence: {conf}%\n\n"
                        f"⚠️ Reason: {reason}\n\n"
                        "⏳ Waiting for stronger confirmation..."
                    )

                for uid in recipients:
                    try:
                        await application.bot.send_message(chat_id=uid, text=msg)
                    except Exception as e:
                        log.warning("Telegram scan message send failed: %s", e)

                log.info(
                    "5-minute scan completed: %s signal=%s confidence=%s",
                    pair, result["signal"], result.get("confidence", 0)
                )

        except Exception:
            log.exception("Scanner loop error")

        elapsed = time.time() - cycle_started
        await asyncio.sleep(max(1, SCAN_INTERVAL_SECONDS - elapsed))

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
    log.info("SIGNAL AI START: pair=%s", pair)
    try:
        ai, ai_err = await asyncio.wait_for(
            asyncio.to_thread(call_ai, ai_prompt(result)),
            timeout=90,
        )
    except asyncio.TimeoutError:
        ai, ai_err = None, "AI validation timed out after 90 seconds"
    log.info("SIGNAL AI END: pair=%s error=%s", pair, ai_err)
    if ai_err:
        await msg.edit_text(
            "🚫 NO SIGNAL\n\n"
            f"📈 {pair}\n"
            f"❌ AI validation unavailable: {ai_err}\n\n"
            "No trade signal generated."
        )
        return

    final_text = format_signal(result, ai)
    log.info(
        "SIGNAL FINAL: pair=%s decision=%s direction=%s confidence=%s",
        pair,
        ai.get("decision", "N/A") if isinstance(ai, dict) else "N/A",
        ai.get("direction", "N/A") if isinstance(ai, dict) else "N/A",
        ai.get("confidence", "N/A") if isinstance(ai, dict) else "N/A",
    )
    await msg.edit_text(final_text)
# ============================================================
# BACKGROUND SCANNER
# ============================================================

async def scan_loop(application):
    """
    Automatic 5-minute signal cycle.

    Every 5 minutes:
      1. Read fresh OlympTrade candles.
      2. Run technical/price-action analysis.
      3. Send the candidate to AI validation when available.
      4. Send an approved UP/DOWN signal to Telegram.

    Important: the 5-minute cycle is guaranteed; a trade direction is NOT
    fabricated when the data/AI rejects the setup. In that case the bot sends
    NO SIGNAL rather than inventing a direction.
    """
    await asyncio.sleep(20)

    while True:
        cycle_started = time.time()

        try:
            for pair in PAIRS:
                df, err = await get_ot_candles(pair, 60, 260)

                if err:
                    log.warning("5-minute scan skipped for %s: %s", pair, err)
                    continue

                result = analyze(df, pair)

                with state_lock:
                    latest_candles[pair] = df
                    latest_signal[pair] = result

                # Always run the AI when a directional candidate exists.
                # The AI cooldown prevents hammering free providers.
                if result["signal"] != "NO SIGNAL":
                    log.info("5-minute AI START: pair=%s", pair)
                    try:
                        ai, ai_err = await asyncio.wait_for(
                            asyncio.to_thread(call_ai, ai_prompt(result)),
                            timeout=90,
                        )
                    except asyncio.TimeoutError:
                        ai, ai_err = None, "AI validation timed out after 90 seconds"
                    log.info("5-minute AI END: pair=%s error=%s", pair, ai_err)
                    if ai_err or not ai:
                        log.warning(
                            "5-minute AI validation unavailable for %s: %s",
                            pair, ai_err
                        )
                        continue

                    direction = str(ai.get("direction", "NO SIGNAL")).upper()
                    decision = str(ai.get("decision", "REJECT")).upper()

                    try:
                        conf = int(ai.get("confidence", 0))
                    except (TypeError, ValueError):
                        conf = 0

                    if (
                        decision == "APPROVE"
                        and direction in ("UP", "DOWN")
                        and direction == result["signal"]
                        and conf >= AI_MIN_CONFIDENCE
                    ):
                        text = format_signal(result, ai)

                        for uid in list(authorized_users):
                            try:
                                await application.bot.send_message(
                                    chat_id=uid,
                                    text=text
                                )
                            except Exception as e:
                                log.warning(
                                    "Telegram signal send failed: %s", e
                                )
                    else:
                        log.info(
                            "5-minute candidate rejected by AI: %s %s conf=%s",
                            pair, direction, conf
                        )

                log.info(
                    "5-minute scan completed: %s signal=%s confidence=%s",
                    pair, result["signal"], result["confidence"]
                )

        except Exception:
            log.exception("5-minute scanner loop error")

        # Keep the cycle aligned to approximately every 5 minutes without
        # creating a tight loop after a slow API/analysis call.
        elapsed = time.time() - cycle_started
        sleep_for = max(1, SCAN_INTERVAL_SECONDS - elapsed)
        await asyncio.sleep(sleep_for)

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

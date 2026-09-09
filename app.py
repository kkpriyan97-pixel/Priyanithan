import asyncio
import io
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from flask import Flask, request
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
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
OLYMPTRADE_ACCESS_TOKEN = os.getenv("OLYMPTRADE_ACCESS_TOKEN")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
AIRFORCE_API_KEY = os.getenv("AIRFORCE_API_KEY")
AIRFORCE_MODEL = os.getenv("AIRFORCE_MODEL", "gpt-oss-120b")

AI_MIN_CONFIDENCE = int(os.getenv("AI_MIN_CONFIDENCE", "60"))
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))
LIVE_UPDATE_SECONDS = 15
AUTO_TRADE = False
MARTINGALE = False

PAIR_ENV = os.getenv("OLYMP_PAIRS", "AUTO")
MANUAL_PAIRS = [x.strip().upper() for x in PAIR_ENV.split(",") if x.strip() and x.strip().upper() != "AUTO"]
PAIRS = MANUAL_PAIRS[:] if MANUAL_PAIRS else []
AUTO_DISCOVER_ASSETS = os.getenv("AUTO_DISCOVER_ASSETS", "true").lower() in ("1", "true", "yes", "on")
MAX_ASSETS_PER_CYCLE = int(os.getenv("MAX_ASSETS_PER_CYCLE", "120"))
MAX_AI_CANDIDATES = min(int(os.getenv("MAX_AI_CANDIDATES", "2")), 2)
MAX_SIGNALS_PER_CYCLE = int(os.getenv("MAX_SIGNALS_PER_CYCLE", "3"))
PAIR_ALIASES = {
    "ASIA_X": os.getenv("OT_ASIA_X_PAIR", "ASIA_X"),
    "EURUSD": os.getenv("OT_EURUSD_PAIR", "EURUSD"),
    "GBPUSD": os.getenv("OT_GBPUSD_PAIR", "GBPUSD"),
}

UAE_TZ = ZoneInfo("Asia/Dubai")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("priyanithan")
APP_VERSION = "6.0-FINAL-LIVE-UAE-WEBHOOK"

app = Flask(__name__)
authorized_users = set()
known_chat_ids = set()
ot_client = None
runtime_loop = None
latest_candles = {}
latest_ticks = {}
latest_signal = {}
manual_trades = {}
discovered_assets = {}
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

telegram_application = None

@app.post("/telegram/webhook")
def telegram_webhook():
    """Receive Telegram updates over HTTPS without getUpdates polling."""
    if telegram_application is None or runtime_loop is None:
        return "Bot is starting", 503
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return "Bad Request", 400
    try:
        update = Update.de_json(payload, telegram_application.bot)
        future = asyncio.run_coroutine_threadsafe(
            telegram_application.update_queue.put(update), runtime_loop
        )
        future.result(timeout=5)
        return "OK", 200
    except Exception as e:
        log.warning("Telegram webhook update failed: %s", e)
        return "Webhook processing failed", 500

# ============================================================
# TIME
# ============================================================
def now_uae():
    return datetime.now(UAE_TZ)

def format_uae_timestamp(ts):
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(UAE_TZ).strftime("%H:%M:%S UAE")
    except Exception:
        return now_uae().strftime("%H:%M:%S UAE")

async def wait_until_next_5min_uae():
    """Align automatic scans to UAE wall-clock :00/:05/:10/..."""
    now = now_uae()
    next_minute = ((now.minute // 5) + 1) * 5
    if next_minute >= 60:
        target = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    else:
        target = now.replace(minute=next_minute, second=0, microsecond=0)
    seconds = max(1, (target - now).total_seconds())
    log.info("Next 5-minute UAE scan at %s (in %.1fs)", target.strftime("%H:%M:%S"), seconds)
    await asyncio.sleep(seconds)

# ============================================================
# RECIPIENTS / AUTH
# ============================================================
def remember_chat(update):
    chat = getattr(update, "effective_chat", None)
    if chat and getattr(chat, "id", None) is not None:
        known_chat_ids.add(int(chat.id))

def is_authorized(update):
    user = getattr(update, "effective_user", None)
    return bool(user and user.id in authorized_users)

def recipients():
    ids = set(authorized_users)
    if TELEGRAM_CHAT_ID:
        try:
            ids.add(int(TELEGRAM_CHAT_ID))
        except ValueError:
            log.warning("TELEGRAM_CHAT_ID is not an integer")
    return list(ids)

async def send_to_recipients(bot, text):
    ids = recipients()
    if not ids:
        log.warning("AUTO SCAN: no Telegram recipient. Use /access YOUR_CODE once, or set TELEGRAM_CHAT_ID in Render.")
        return False
    try:
        bot_id = int((await bot.get_me()).id)
    except Exception:
        bot_id = None
    sent = False
    for chat_id in ids:
        if bot_id is not None and int(chat_id) == bot_id:
            log.warning("Telegram recipient %s is the bot's own ID; skipped", chat_id)
            continue
        try:
            await bot.send_message(chat_id=chat_id, text=text)
            sent = True
        except Exception as e:
            log.warning("Telegram send failed chat_id=%s: %s", chat_id, e)
    return sent

# ============================================================
# CANDLE NORMALIZATION
# ============================================================
def normalize_candles(raw):
    # Supports the observed OlympTrade response shape:
    # {'d':[{'pair':..., 'tf':60, 'candles':[...]}], 'e':10, ...}
    if isinstance(raw, dict):
        d = raw.get("d")
        if isinstance(d, list) and d and isinstance(d[0], dict):
            raw = d[0].get("candles", d)
        else:
            raw = d
    if not isinstance(raw, list):
        return None
    rows = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        o = c.get("open", c.get("o"))
        h = c.get("high", c.get("h"))
        l = c.get("low", c.get("l"))
        cl = c.get("close", c.get("c"))
        ts = c.get("timestamp", c.get("t", c.get("time")))
        if None in (o, h, l, cl):
            continue
        try:
            rows.append({
                "timestamp": float(ts) if ts is not None else time.time(),
                "open": float(o), "high": float(h), "low": float(l), "close": float(cl),
                "volume": float(c.get("volume", c.get("v", 0)) or 0),
            })
        except (TypeError, ValueError):
            continue
    if not rows:
        return None
    return pd.DataFrame(rows).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

# ============================================================
# OLYMPTRADE READ-ONLY CONNECTION
# ============================================================
async def on_tick(message):
    data = message.get("d", []) if isinstance(message, dict) else []
    if not isinstance(data, list):
        return
    for item in data:
        if not isinstance(item, dict):
            continue
        pair = str(item.get("pair") or item.get("symbol") or "").upper()
        if pair:
            latest_ticks[pair] = item


async def on_balance(message):
    """Read-only OlympTrade balance callback; never places or modifies trades."""
    log.debug("OlympTrade balance update received (read-only).")


def extract_instruments(message):
    found = {}
    data = message.get("d") if isinstance(message, dict) else None
    if isinstance(data, dict):
        data = data.get("instruments", data.get("pairs", data.get("items", [])))
    if not isinstance(data, list):
        return found
    for item in data:
        if not isinstance(item, dict):
            continue
        pair = str(item.get("pair") or item.get("symbol") or item.get("name") or item.get("id") or "").upper().strip()
        if not pair:
            continue
        found[pair] = item
    return found

async def on_instruments(message):
    found = extract_instruments(message)
    if found:
        discovered_assets.update(found)
        if AUTO_DISCOVER_ASSETS:
            PAIRS[:] = sorted(discovered_assets.keys())[:MAX_ASSETS_PER_CYCLE]
            log.info("AUTO DISCOVERY: %s tradable assets available", len(PAIRS))

async def on_trade_event(message):
    """Record broker trade events for read-only monitoring; never executes trades."""
    event = message.get("e") if isinstance(message, dict) else None
    data = message.get("d", []) if isinstance(message, dict) else []
    if not isinstance(data, list):
        return
    for item in data:
        if not isinstance(item, dict):
            continue
        trade_id = item.get("id")
        if not trade_id:
            continue
        manual_trades.setdefault(str(trade_id), {}).update({
            "event": event,
            "data": item,
            "updated_at": time.time(),
        })


async def olymptrade_connect_loop():
    global ot_client, PAIRS
    if not OLYMPTRADE_ACCESS_TOKEN:
        log.error("OLYMPTRADE_ACCESS_TOKEN is not configured.")
        return
    retry_delay = 5
    attempt = 0
    while True:
        client = None
        try:
            attempt += 1
            log.info("OlympTrade connection attempt #%s", attempt)
            client = OlympTradeClient(access_token=OLYMPTRADE_ACCESS_TOKEN, log_raw_messages=False)
            client.register_callback(parameters.E_TICK_UPDATE, on_tick)
            client.register_callback(parameters.E_BALANCE_UPDATE, on_balance)
            # Instrument catalogue observed in the project logs (event 1054).
            # Register before start so the initial catalogue is not missed.
            client.register_callback(1054, on_instruments)
            client.register_callback(parameters.E_TRADE_ACCEPTED, on_trade_event)
            client.register_callback(parameters.E_TRADE_UPDATE_INTERIM, on_trade_event)
            client.register_callback(parameters.E_TRADE_CLOSED, on_trade_event)
            await client.start()
            ot_client = client

            # Balance is optional. A timeout here must NOT kill market data.
            try:
                await asyncio.wait_for(client.balance.get_balance(), timeout=10)
            except Exception as e:
                log.warning("Balance read skipped/failed: %s", e)

            # If auto discovery is enabled, the instrument callback may have populated PAIRS.
            # Manual PAIRS remain supported for testing.
            if not PAIRS and AUTO_DISCOVER_ASSETS:
                log.info("Waiting briefly for broker instrument catalogue...")
                await asyncio.sleep(2)
            if not PAIRS and MANUAL_PAIRS:
                PAIRS = MANUAL_PAIRS[:]
            for pair in PAIRS[:MAX_ASSETS_PER_CYCLE]:
                broker_pair = PAIR_ALIASES.get(pair, pair)
                try:
                    await client.market.subscribe_ticks(broker_pair)
                    log.info("Subscribed to OlympTrade ticks: %s", broker_pair)
                except Exception as e:
                    log.warning("Tick subscription failed for %s: %s", broker_pair, e)
            log.info("OlympTrade connection established. Assets available for scanner: %s", len(discovered_assets) if AUTO_DISCOVER_ASSETS else len(PAIRS))
            retry_delay = 5
            attempt = 0
            while client.connection.is_connected:
                await asyncio.sleep(5)
            raise ConnectionError("OlympTrade WebSocket disconnected")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("OlympTrade connection error: %s", e)
            ot_client = None
            try:
                if client and client.connection.is_connected:
                    await client.connection.disconnect()
            except Exception:
                pass
            log.warning("OlympTrade reconnecting in %s seconds", retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)

async def get_ot_candles(pair, timeframe=60, count=120):
    if ot_client is None:
        return None, "OlympTrade client unavailable"
    log.info("CANDLE REQUEST START: pair=%s timeframe=%s count=%s", pair, timeframe, count)
    try:
        raw = await ot_client.get_candles(pair, timeframe, count)
        log.info("CANDLE RESPONSE RECEIVED: pair=%s raw_type=%s", pair, type(raw).__name__)
    except Exception as e:
        log.warning("CANDLE REQUEST FAILED: pair=%s error=%s", pair, e)
        return None, f"candle request failed: {e}"
    df = normalize_candles(raw)
    if df is None or len(df) < 40:
        return None, f"insufficient candle data rows={0 if df is None else len(df)}"
    now_utc = time.time()
    latest_ts = float(df["timestamp"].iloc[-1])
    if latest_ts > 100000000000:
        latest_ts /= 1000.0
        df["timestamp"] = df["timestamp"] / 1000.0
    age_seconds = now_utc - latest_ts
    log.info("LIVE DATA CHECK: pair=%s latest=%s age=%.1fs UAE=%s", pair, datetime.fromtimestamp(latest_ts, timezone.utc).isoformat(), age_seconds, format_uae_timestamp(latest_ts))
    if latest_ts > now_utc + 120:
        return None, f"Future-dated OlympTrade candle rejected ({age_seconds:.1f}s skew)"
    if age_seconds > 180:
        return None, f"STALE OlympTrade candles rejected ({age_seconds:.1f}s old)"
    log.info("LIVE CANDLES READY: pair=%s rows=%s latest=%s", pair, len(df), format_uae_timestamp(latest_ts))
    return df, None

# ============================================================
# TECHNICAL ANALYSIS
# ============================================================
def analyze_pair(pair, df):
    close = df["close"]
    high = df["high"]
    low = df["low"]
    ema9 = EMAIndicator(close, window=9).ema_indicator()
    ema21 = EMAIndicator(close, window=21).ema_indicator()
    macd_obj = MACD(close)
    macd = macd_obj.macd()
    macd_signal = macd_obj.macd_signal()
    rsi = RSIIndicator(close, window=14).rsi()
    stoch = StochasticOscillator(high, low, close).stoch()
    adx = ADXIndicator(high, low, close).adx()
    bb = BollingerBands(close)
    bb_high = bb.bollinger_hband()
    bb_low = bb.bollinger_lband()

    c = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    e9 = float(ema9.iloc[-1])
    e21 = float(ema21.iloc[-1])
    m = float(macd.iloc[-1])
    ms = float(macd_signal.iloc[-1])
    rv = float(rsi.iloc[-1])
    sv = float(stoch.iloc[-1])
    av = float(adx.iloc[-1])
    bh = float(bb_high.iloc[-1])
    bl = float(bb_low.iloc[-1])

    score_up = 0
    score_down = 0
    reasons = []
    patterns = []
    if e9 > e21:
        score_up += 2
        reasons.append("EMA bullish")
    elif e9 < e21:
        score_down += 2
        reasons.append("EMA bearish")
    if m > ms:
        score_up += 2
        reasons.append("MACD bullish")
    elif m < ms:
        score_down += 2
        reasons.append("MACD bearish")
    if rv >= 52 and rv < 70:
        score_up += 1
    elif rv <= 48 and rv > 30:
        score_down += 1
    if c > bh:
        score_down += 1
        reasons.append("above upper BB")
    elif c < bl:
        score_up += 1
        reasons.append("below lower BB")
    if sv > 80:
        score_down += 1
        reasons.append("stoch overbought")
    elif sv < 20:
        score_up += 1
        reasons.append("stoch oversold")
    if av >= 25:
        if score_up > score_down:
            score_up += 2
        elif score_down > score_up:
            score_down += 2
    elif av >= 20:
        if score_up > score_down:
            score_up += 1
        elif score_down > score_up:
            score_down += 1
    body = abs(c - prev)
    if body > 0:
        if c > prev:
            patterns.append("bullish close")
        else:
            patterns.append("bearish close")
    signal = "UP" if score_up > score_down else ("DOWN" if score_down > score_up else "NO SIGNAL")
    confidence = int(min(99, 50 + abs(score_up - score_down) * 7 + max(0, av - 20)))
    if av < 15:
        confidence = min(confidence, 62)
    candle_time = format_uae_timestamp(float(df["timestamp"].iloc[-1]))
    reason = "; ".join(reasons[:5]) or "mixed indicators"
    return {
        "pair": pair,
        "signal": signal,
        "confidence": confidence,
        "candle_time": candle_time,
        "patterns": patterns,
        "trend": "BULLISH" if score_up > score_down else ("BEARISH" if score_down > score_up else "MIXED"),
        "rsi": rv,
        "adx": av,
        "reason": reason,
        "price": c,
        "ema9": e9,
        "ema21": e21,
        "macd": m,
        "macd_signal": ms,
        "stoch": sv,
        "bb_high": bh,
        "bb_low": bl,
    }


def choose_duration_min(result, ai):
    requested = ai.get("duration_min", 5) if isinstance(ai, dict) else 5
    try:
        requested = int(requested)
    except Exception:
        requested = 5
    allowed = [1, 2, 3, 5, 10, 15]
    return requested if requested in allowed else 5

# ============================================================
# AI
# ============================================================
def ai_prompt(result):
    return f"""You are a cautious OlympTrade market-setup validator. Do not invent data.
Evaluate price action, candle patterns, market structure, EMA, MACD, RSI, Bollinger Bands,
Stochastic, ADX, support/resistance and conflicts.
Rules:
- ADX below 15 is strong caution; 15-20 reduces confidence but is not automatic rejection.
- Reject when there are multiple major conflicts or the direction is genuinely mixed.
- A single caution (weak ADX, overbought/oversold, or nearby S/R) is not by itself enough to reject.
- One candle pattern alone is never sufficient.
- If at least 3 independent factors support the same direction and there is no major conflict, APPROVE can be used.
- Do not require every indicator to agree; markets can be valid while one indicator is neutral or cautionary.
- Confidence is a validation score, NOT a guaranteed win probability.
- If approving, choose the most suitable expiry from exactly: 1, 2, 3, 5, 10, 15 minutes.
- Choose expiry from setup quality, momentum, volatility, candle structure, trend strength and support/resistance distance.
- Do not choose a longer expiry merely to avoid NO SIGNAL.
- If the setup is conflicted or unsafe, reject it.
Return JSON only:
{{"decision":"APPROVE|REJECT","direction":"UP|DOWN|NO SIGNAL","confidence":0-100,"duration_min":1|2|3|5|10|15,"reason":"short reason"}}
DATA:
{json.dumps(result, ensure_ascii=False)}""".strip()


def _json_object_from_text(text):
    text = str(text or "").strip()
    if not text:
        raise ValueError("AI response contained no text content")
    text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```\s*$", "", text)
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
            if isinstance(value, dict):
                return value
        except Exception:
            continue
    raise ValueError(f"AI response was not valid JSON: {text[:300]}")


def parse_ai(data):
    """Parse OpenAI-compatible responses and find the actual decision JSON."""
    if not isinstance(data, dict):
        raise ValueError("AI response is not a JSON object")
    choices = data.get("choices")
    if not choices or not isinstance(choices[0], dict):
        raise ValueError(f"AI response missing choices: {str(data)[:500]}")
    choice = choices[0]
    msg = choice.get("message") if isinstance(choice.get("message"), dict) else {}

    values = [msg.get("content"), choice.get("text"), data.get("output_text"), data.get("response"), data.get("result"), msg.get("reasoning_content"), msg.get("reasoning")]
    candidates = []
    for value in values:
        if isinstance(value, list):
            value = "".join(str(x.get("text") or x.get("content") or "") if isinstance(x, dict) else str(x) for x in value)
        if value:
            candidates.append(value if isinstance(value, dict) else str(value))

    for value in candidates:
        if isinstance(value, dict) and "decision" in value:
            return value
        if not isinstance(value, str):
            continue
        text = value.strip()
        text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```\s*$", "", text)
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and "decision" in obj:
                return obj
        except (json.JSONDecodeError, TypeError):
            pass
        # Reasoning may contain several {...} fragments; only accept one with decision.
        for match in re.finditer(r'\{', text):
            try:
                obj, _ = json.JSONDecoder().raw_decode(text[match.start():])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(obj, dict) and "decision" in obj:
                return obj

    raise ValueError("AI response contained no valid decision JSON")

def call_ai(prompt):
    """Validate a setup with independent AI providers and fail over automatically."""
    log.info("AI FALLBACK CHAIN START: prompt_chars=%s", len(str(prompt)))
    providers = []

    if OPENROUTER_API_KEY:
        models = []
        for m in (OPENROUTER_MODEL, "openrouter/free", "minimax/minimax-m3:free", "google/gemma-4-26b-a4b-it:free"):
            if m and m not in models:
                models.append(m)
        for m in models:
            providers.append((f"OpenRouter/{m}", "https://openrouter.ai/api/v1/chat/completions", OPENROUTER_API_KEY, m))

    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        providers.append(("Groq", "https://api.groq.com/openai/v1/chat/completions", groq_key, os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")))

    cerebras_key = os.getenv("CEREBRAS_API_KEY")
    if cerebras_key:
        providers.append(("Cerebras", "https://api.cerebras.ai/v1/chat/completions", cerebras_key, os.getenv("CEREBRAS_MODEL", "llama3.1-8b")))

    mistral_key = os.getenv("MISTRAL_API_KEY")
    if mistral_key:
        providers.append(("Mistral", "https://api.mistral.ai/v1/chat/completions", mistral_key, os.getenv("MISTRAL_MODEL", "mistral-small-latest")))

    if AIRFORCE_API_KEY:
        providers.append(("Airforce", "https://api.airforce/v1/chat/completions", AIRFORCE_API_KEY, AIRFORCE_MODEL))

    if not providers:
        log.error("AI FALLBACK CHAIN SKIPPED: no AI provider configured")
        return None, "No AI provider configured"

    last_error = None
    for index, (name, url, key, model) in enumerate(providers, start=1):
        try:
            log.info("AI PROVIDER TRY %s/%s: %s model=%s", index, len(providers), name, model)
            headers = {"Authorization": f"Bearer {key.strip()}", "Content-Type": "application/json"}
            if name.startswith("OpenRouter/"):
                headers["HTTP-Referer"] = "https://priyanithan-ai.onrender.com"
                headers["X-Title"] = "Priyanithan AI OlympTrade Signal Bot"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "Return exactly one valid JSON object and no reasoning/prose/markdown."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": 300,
                "response_format": {"type": "json_object"},
            }
            started = time.time()
            r = requests.post(url, headers=headers, json=payload, timeout=15)
            elapsed = time.time() - started
            log.info("AI PROVIDER RESPONSE: %s model=%s status=%s elapsed=%.2fs bytes=%s", name, model, r.status_code, elapsed, len(r.content))
            if not r.ok:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500].replace(chr(10), ' ')}")
            try:
                body = r.json()
            except Exception as e:
                raise RuntimeError(f"invalid provider JSON envelope: {e}; body={r.text[:300]}")
            parsed = parse_ai(body)
            log.info("AI PROVIDER SUCCESS: %s model=%s decision=%s direction=%s confidence=%s", name, model, parsed.get("decision"), parsed.get("direction"), parsed.get("confidence"))
            return parsed, None
        except Exception as e:
            last_error = f"{name}: {e}"
            log.warning("AI PROVIDER FAILED: %s", last_error)
            if index < len(providers):
                log.warning("AI FAILOVER: switching from %s to %s", name, providers[index][0])

    log.error("AI FALLBACK CHAIN EXHAUSTED: %s", last_error or "unknown error")
    return None, last_error or "All AI providers failed"

# ============================================================
# AI DECISION SAFETY
# ============================================================
def normalize_ai_decision(ai):
    """Accept only exact APPROVE/REJECT decisions; never guess a typo."""
    if not isinstance(ai, dict):
        return ai
    raw = str(ai.get("decision", "")).strip().upper()
    if raw in ("APPROVE", "REJECT"):
        ai["decision"] = raw
        return ai
    ai["decision"] = "REJECT"
    ai["decision_error"] = f"Invalid AI decision '{raw or 'EMPTY'}'; approval not assumed."
    return ai

# ============================================================
# TELEGRAM FORMAT
# ============================================================
def format_signal(result, ai=None):
    ai = normalize_ai_decision(ai) if ai else ai
    if result["signal"] == "NO SIGNAL" or not ai:
        return ("🚫 NO SIGNAL\n\n"
                f"📈 {result['pair']}\n🕐 {result['candle_time']}\n\n"
                f"🕯️ Patterns: {', '.join(result['patterns']) or 'None'}\n"
                f"📈 Trend: {result['trend']}\n📊 RSI: {result['rsi']:.1f}\n💪 ADX: {result['adx']:.1f}\n\n"
                f"⚠️ {result['reason']}\n⏳ Waiting for stronger setup...")
    decision = str(ai.get("decision", "REJECT")).upper()
    direction = str(ai.get("direction", "NO SIGNAL")).upper()
    try: conf = int(ai.get("confidence", 0))
    except: conf = 0
    if decision != "APPROVE" or direction not in ("UP","DOWN") or direction != result["signal"] or conf < AI_MIN_CONFIDENCE:
        return ("🚫 NO SIGNAL\n\n"
                f"📈 {result['pair']}\n🕐 {result['candle_time']}\n\n"
                f"📊 Technical: {result['signal']} ({result['confidence']}%)\n"
                f"🤖 AI: {decision} / {direction} / {conf}%\n"
                f"⚠️ {ai.get('decision_error', ai.get('reason','AI rejected the setup.'))}\n\n"
                "⏳ Waiting for stronger confirmation...")
    fire = "🔥🔥🔥" if conf >= 94 else ("🔥🔥" if conf >= 91 else "🔥")
    arrow = "⬆️" if direction == "UP" else "⬇️"
    duration = f"{choose_duration_min(result, ai)} MIN"
    return (f"{fire} PRIYANITHAN AI SIGNAL {fire}\n\n"
            f"📈 {result['pair']}\n{arrow} {direction}\n💰 Entry: {result['price']}\n⏱️ Expiry: {duration}\n"
            f"🤖 AI Confidence: {conf}%\n📊 Technical: {result['confidence']}%\n"
            f"🕐 {result['candle_time']}\n🧠 Candice AI: APPROVED\n\n"
            "⚠️ MANUAL TRADE — AUTO TRADE OFF")

# ============================================================
# SCAN / TELEGRAM COMMANDS
# ============================================================
async def scan_cycle(application):
    universe = sorted(discovered_assets.keys())[:MAX_ASSETS_PER_CYCLE] if AUTO_DISCOVER_ASSETS else (MANUAL_PAIRS[:] if MANUAL_PAIRS else PAIRS[:MAX_ASSETS_PER_CYCLE])
    candidates = []
    candle_ready = 0
    candle_failed = 0
    technical_no_signal = 0
    log.info("SCAN START: universe=%s auto_discovery=%s", len(universe), AUTO_DISCOVER_ASSETS)
    for idx, pair in enumerate(universe, start=1):
        log.info("CANDLE SCAN %s/%s: requesting %s", idx, len(universe), pair)
        df, err = await get_ot_candles(pair, 60, 120)
        if df is None:
            candle_failed += 1
            log.warning("CANDLE SKIP: pair=%s reason=%s", pair, err or "unknown candle failure")
            continue
        candle_ready += 1
        try:
            result = analyze_pair(pair, df)
        except Exception as e:
            log.warning("Technical analysis failed pair=%s: %s", pair, e)
            continue
        log.info("TECHNICAL RESULT: pair=%s signal=%s confidence=%s", pair, result.get("signal"), result.get("confidence"))
        if result["signal"] != "NO SIGNAL":
            candidates.append(result)
            log.info("TECHNICAL CANDIDATE: pair=%s signal=%s confidence=%s", pair, result["signal"], result["confidence"])
        else:
            technical_no_signal += 1
    log.info("CANDLE/TECHNICAL SUMMARY: universe=%s candle_ready=%s candle_failed=%s technical_candidates=%s technical_no_signal=%s", len(universe), candle_ready, candle_failed, len(candidates), technical_no_signal)
    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    checked = candidates[:MAX_AI_CANDIDATES]
    approved = []
    for result in checked:
        ai, err = call_ai(ai_prompt(result))
        if err:
            log.info("AI DECISION DETAIL: pair=%s decision=ERROR direction=NO SIGNAL confidence=0 duration=%s reason=%s", result["pair"], choose_duration_min(result, None), err)
            continue
        ai = normalize_ai_decision(ai)
        direction = str(ai.get("direction", "NO SIGNAL")).upper()
        try: conf = int(ai.get("confidence", 0))
        except Exception: conf = 0
        log.info("AI DECISION DETAIL: pair=%s decision=%s direction=%s confidence=%s duration=%s reason=%s", result["pair"], ai.get("decision"), direction, conf, choose_duration_min(result, ai), ai.get("reason", ""))
        if ai.get("decision") == "APPROVE" and direction == result["signal"] and conf >= AI_MIN_CONFIDENCE:
            approved.append((result, ai))
            if len(approved) >= MAX_SIGNALS_PER_CYCLE:
                break
    if approved:
        for result, ai in approved:
            await send_to_recipients(application.bot, format_signal(result, ai))
    else:
        await send_to_recipients(application.bot, f"🚫 NO QUALIFIED SIGNAL\n\n🔎 Assets scanned: {len(universe)}\n🤖 AI candidates checked: {len(checked)}\n⏳ Waiting for a stronger setup on the next 5-minute cycle.")
    log.info("5-minute cycle complete: %s approved signal(s); universe=%s candidates=%s", len(approved), len(universe), len(checked))

async def access_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remember_chat(update)
    if not ACCESS_CODE:
        await update.message.reply_text("ACCESS_CODE is not configured.")
        return
    args = context.args or []
    if args and args[0].strip() == ACCESS_CODE:
        user = getattr(update, "effective_user", None)
        if user:
            authorized_users.add(int(user.id))
            await update.message.reply_text("✅ Access approved. This chat can receive signals.")
        return
    await update.message.reply_text("❌ Invalid access code.")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remember_chat(update)
    await update.message.reply_text("Priyanithan AI is online. Use /access YOUR_CODE to authorize this chat.")

async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remember_chat(update)
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized. Use /access YOUR_CODE first.")
        return
    await update.message.reply_text("🔎 Live scan started...")
    await scan_cycle(context.application)

async def scan_loop(application):
    """Run the existing scan_cycle on UAE-aligned 5-minute boundaries."""
    await wait_until_next_5min_uae()
    while True:
        try:
            await scan_cycle(application)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scanner loop error")
        await wait_until_next_5min_uae()


async def manual_trade_monitor(application):
    while True:
        try:
            client = ot_client
            account_id = getattr(client, "account_id", None) if client else None
            if client and client.connection.is_connected and account_id:
                try:
                    trades = await client.trade.get_open_trades(account_id, group="real")
                    if isinstance(trades, list):
                        for t in trades:
                            if isinstance(t, dict) and t.get("id"):
                                manual_trades.setdefault(str(t["id"]), {})["open"] = t
                except Exception as e:
                    log.debug("Open-trade read unavailable: %s", e)
        except Exception:
            log.exception("Manual monitor error")
        await asyncio.sleep(LIVE_UPDATE_SECONDS)


# ============================================================
# RUNTIME
# ============================================================
async def telegram_runtime(application):
    global runtime_loop, telegram_application
    runtime_loop = asyncio.get_running_loop()
    telegram_application = application
    await application.initialize()
    await application.start()
    tasks = [
        asyncio.create_task(olymptrade_connect_loop(), name="olymptrade-connect"),
        asyncio.create_task(scan_loop(application), name="signal-scan"),
        asyncio.create_task(manual_trade_monitor(application), name="trade-monitor"),
    ]
    try:
        webhook_base = os.getenv("RENDER_EXTERNAL_URL", "https://priyanithan-ai.onrender.com").rstrip("/")
        webhook_url = f"{webhook_base}/telegram/webhook"
        await application.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=["message"],
        )
        log.info("Telegram webhook active: %s", webhook_url)
        log.info("Telegram polling DISABLED; getUpdates will not be used.")
        await asyncio.Event().wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if application.running:
            await application.stop()
        await application.shutdown()

def main():
    global ot_client
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).updater(None).build()
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("access", access_cmd))
    application.add_handler(CommandHandler("scan", scan_cmd))
    # The project-specific OlympTrade client remains read-only; AUTO_TRADE is hard-disabled.
    ot_client = OlympTradeClient(OLYMPTRADE_ACCESS_TOKEN, parameters)
    asyncio.run(telegram_runtime(application))


def start_web_server():
    """Keep the Render Web Service port open for health checks while the bot runs."""
    port = int(os.getenv("PORT", "10000"))
    log.info("Render health server starting on 0.0.0.0:%s", port)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True, name="render-health").start()
    main()

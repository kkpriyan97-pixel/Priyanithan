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
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
OLYMPTRADE_ACCESS_TOKEN = os.getenv("OLYMPTRADE_ACCESS_TOKEN")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
AIRFORCE_API_KEY = os.getenv("AIRFORCE_API_KEY")
AIRFORCE_MODEL = os.getenv("AIRFORCE_MODEL", "gpt-oss-120b")

AI_MIN_CONFIDENCE = int(os.getenv("AI_MIN_CONFIDENCE", "89"))
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))
LIVE_UPDATE_SECONDS = 15
AUTO_TRADE = False
MARTINGALE = False

PAIR_ENV = os.getenv("OLYMP_PAIRS", "ASIA_X")
PAIRS = [x.strip().upper() for x in PAIR_ENV.split(",") if x.strip()]
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
APP_VERSION = "3.0-uae-auto-signal-readonly"

app = Flask(__name__)
authorized_users = set()
known_chat_ids = set()
ot_client = None
runtime_loop = None
latest_candles = {}
latest_ticks = {}
latest_signal = {}
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
    sent = False
    for chat_id in ids:
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
    for tick in data:
        if not isinstance(tick, dict):
            continue
        pair = tick.get("p", tick.get("pair"))
        price = tick.get("q", tick.get("price"))
        ts = tick.get("t", tick.get("timestamp", time.time()))
        if pair is None or price is None:
            continue
        try:
            with state_lock:
                latest_ticks[str(pair).upper()] = {"price": float(price), "timestamp": float(ts)}
        except (TypeError, ValueError):
            pass

async def on_balance(message):
    log.debug("OlympTrade balance update received (read-only).")

async def on_trade_event(message):
    event = message.get("e") if isinstance(message, dict) else None
    data = message.get("d", []) if isinstance(message, dict) else []
    if not isinstance(data, list):
        return
    for item in data:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        with state_lock:
            rec = manual_trades.setdefault(str(item["id"]), {})
            rec["event"] = event
            rec["data"] = item
            rec["updated_at"] = time.time()

async def olymptrade_connect_loop():
    global ot_client
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

            for pair in PAIRS:
                broker_pair = PAIR_ALIASES.get(pair, pair)
                try:
                    await client.market.subscribe_ticks(broker_pair)
                    log.info("Subscribed to OlympTrade ticks: %s", broker_pair)
                except Exception as e:
                    log.warning("Tick subscription failed for %s: %s", broker_pair, e)
            log.info("OlympTrade connection established for: %s", PAIRS)
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

async def get_ot_candles(pair, size=60, count=260):
    client = ot_client
    broker_pair = PAIR_ALIASES.get(pair, pair)
    if client is None or not client.connection.is_connected:
        return None, "OlympTrade WebSocket is not connected"
    try:
        log.info("CANDLE REQUEST: pair=%s broker_pair=%s size=%s count=%s", pair, broker_pair, size, count)
        raw = await client.market.get_candles(broker_pair, size=size, count=count)
        df = normalize_candles(raw)
        rows = len(df) if df is not None else 0
        log.info("CANDLE NORMALIZED: pair=%s rows=%s", pair, rows)
        if df is None or rows < 220:
            return None, f"Not enough OlympTrade candles ({rows}/220)"
        log.info("HISTORICAL CANDLES READY: %s rows for %s", rows, pair)
        return df.tail(count).reset_index(drop=True), None
    except Exception as e:
        log.exception("Historical candle request failed for %s", pair)
        return None, str(e)

# ============================================================
# TECHNICAL / PRICE ACTION
# ============================================================
def candle_patterns(df):
    a, b = df.iloc[-2], df.iloc[-3]
    body = abs(a.close - a.open)
    rng = max(a.high - a.low, 1e-12)
    upper = a.high - max(a.open, a.close)
    lower = min(a.open, a.close) - a.low
    bull, bear = a.close > a.open, a.close < a.open
    r = {}
    r["Doji"] = body <= rng * .10
    r["Hammer"] = lower >= body * 2 and upper <= max(body, rng * .15)
    r["Inverted Hammer"] = upper >= body * 2 and lower <= max(body, rng * .15)
    r["Shooting Star"] = upper >= body * 2 and lower <= max(body, rng * .15) and bear
    r["Bullish Engulfing"] = bull and b.close < b.open and a.open <= b.close and a.close >= b.open
    r["Bearish Engulfing"] = bear and b.close > b.open and a.open >= b.close and a.close <= b.open
    r["Harami"] = max(a.open, a.close) <= max(b.open, b.close) and min(a.open, a.close) >= min(b.open, b.close)
    r["Pin Bar"] = max(upper, lower) >= rng * .60
    r["Marubozu"] = body >= rng * .80
    r["Spinning Top"] = body <= rng * .35 and upper >= rng * .20 and lower >= rng * .20
    c = df.iloc[-4]
    r["Morning Star"] = c.close < c.open and abs(b.close-b.open) <= abs(c.close-c.open)*.45 and bull and a.close > (c.open+c.close)/2
    r["Evening Star"] = c.close > c.open and abs(b.close-b.open) <= abs(c.close-c.open)*.45 and bear and a.close < (c.open+c.close)/2
    r["Tweezer Bottom"] = abs(a.low-b.low) <= rng*.08 and bull and b.close < b.open
    r["Tweezer Top"] = abs(a.high-b.high) <= rng*.08 and bear and b.close > b.open
    last3 = df.iloc[-4:-1]
    r["Three White Soldiers"] = all(x.close > x.open for _, x in last3.iterrows())
    r["Three Black Crows"] = all(x.close < x.open for _, x in last3.iterrows())
    r["Inside Bar"] = a.high <= b.high and a.low >= b.low
    return [k for k, v in r.items() if v]

def structure(df):
    highs, lows = df.high.tail(30).to_numpy(), df.low.tail(30).to_numpy()
    hh = highs[-1] > highs[-6] if len(highs) >= 6 else False
    hl = lows[-1] > lows[-6] if len(lows) >= 6 else False
    lh = highs[-1] < highs[-6] if len(highs) >= 6 else False
    ll = lows[-1] < lows[-6] if len(lows) >= 6 else False
    if hh and hl: return "Bullish"
    if lh and ll: return "Bearish"
    return "Range / Mixed"

def analyze(df, pair):
    x = df.copy()
    close, high, low = x.close, x.high, x.low
    x["ema9"] = EMAIndicator(close, 9).ema_indicator()
    x["ema21"] = EMAIndicator(close, 21).ema_indicator()
    x["ema50"] = EMAIndicator(close, 50).ema_indicator()
    x["ema200"] = EMAIndicator(close, 200).ema_indicator()
    macd = MACD(close, 26, 12, 9)
    x["macd"] = macd.macd(); x["macd_signal"] = macd.macd_signal(); x["macd_hist"] = macd.macd_diff()
    x["rsi"] = RSIIndicator(close, 14).rsi()
    bb = BollingerBands(close, 20, 2)
    x["bb_mid"] = bb.bollinger_mavg(); x["bb_high"] = bb.bollinger_hband(); x["bb_low"] = bb.bollinger_lband()
    st = StochasticOscillator(high, low, close, 14, 3)
    x["stoch_k"] = st.stoch(); x["stoch_d"] = st.stoch_signal()
    x["adx"] = ADXIndicator(high, low, close, 14).adx()
    row = x.iloc[-2]
    price = float(row.close)
    pats, trend = candle_patterns(x), structure(x)
    bull = bear = 0; reasons = []; conflicts = []
    if row.ema9 > row.ema21: bull += 1; reasons.append("EMA 9/21 Bullish")
    elif row.ema9 < row.ema21: bear += 1; reasons.append("EMA 9/21 Bearish")
    if row.macd > row.macd_signal and row.macd_hist > 0: bull += 1; reasons.append("MACD Bullish")
    elif row.macd < row.macd_signal and row.macd_hist < 0: bear += 1; reasons.append("MACD Bearish")
    if 50 < row.rsi < 70: bull += 1; reasons.append("RSI Momentum")
    elif 30 < row.rsi < 50: bear += 1; reasons.append("RSI Momentum")
    elif row.rsi >= 70: conflicts.append("RSI Overbought")
    elif row.rsi <= 30: conflicts.append("RSI Oversold")
    if price > row.bb_mid: bull += 1; reasons.append("Above Bollinger Mid")
    elif price < row.bb_mid: bear += 1; reasons.append("Below Bollinger Mid")
    if row.stoch_k > row.stoch_d and row.stoch_k < 85: bull += 1; reasons.append("Stochastic Bullish")
    elif row.stoch_k < row.stoch_d and row.stoch_k > 15: bear += 1; reasons.append("Stochastic Bearish")
    if trend == "Bullish": bull += 1; reasons.append("Bullish Structure")
    elif trend == "Bearish": bear += 1; reasons.append("Bearish Structure")
    if any(p in pats for p in ("Bullish Engulfing", "Morning Star", "Three White Soldiers")): bull += 1; reasons.append("Bullish Candle Pattern")
    if any(p in pats for p in ("Bearish Engulfing", "Evening Star", "Three Black Crows")): bear += 1; reasons.append("Bearish Candle Pattern")

    adx_val = float(row.adx)
    strength, margin = max(bull, bear), abs(bull-bear)
    directional = "UP" if bull > bear else ("DOWN" if bear > bull else "NO SIGNAL")
    core_up = row.ema9 > row.ema21 and row.macd > row.macd_signal and row.macd_hist > 0 and trend == "Bullish"
    core_down = row.ema9 < row.ema21 and row.macd < row.macd_signal and row.macd_hist < 0 and trend == "Bearish"
    if directional == "NO SIGNAL" or strength < 3 or margin < 1:
        signal, reason = "NO SIGNAL", "Directional evidence is not strong/coherent enough."
    elif directional == "UP" and not core_up:
        signal, reason = "NO SIGNAL", "EMA, MACD and market structure are not aligned bullish."
    elif directional == "DOWN" and not core_down:
        signal, reason = "NO SIGNAL", "EMA, MACD and market structure are not aligned bearish."
    else:
        signal, reason = directional, "Core EMA + MACD + structure aligned; pending AI validation."
    if adx_val < 15: conflicts.append(f"Very weak ADX ({adx_val:.1f})")
    elif adx_val < 20: conflicts.append(f"Weak ADX ({adx_val:.1f})")
    recent = x.iloc[-22:-2]
    resistance, support = float(recent.high.max()), float(recent.low.min())
    if signal == "UP" and resistance > price and (resistance-price)/max(price,1e-12) < .0008: conflicts.append("Resistance very close")
    if signal == "DOWN" and support < price and (price-support)/max(price,1e-12) < .0008: conflicts.append("Support very close")
    confidence = min(99, int(55 + max(bull,bear)*5 - len(conflicts)*7))
    return {
        "pair": pair, "signal": signal, "price": price,
        "candle_time": format_uae_timestamp(row.timestamp),
        "bull": bull, "bear": bear, "trend": trend, "adx": adx_val,
        "rsi": float(row.rsi), "macd": float(row.macd), "macd_signal": float(row.macd_signal),
        "stoch_k": float(row.stoch_k), "stoch_d": float(row.stoch_d), "patterns": pats,
        "reasons": reasons, "conflicts": conflicts, "support": support, "resistance": resistance,
        "confidence": confidence, "reason": reason, "data_source": "OlympTrade live candle feed",
    }

# ============================================================
# AI
# ============================================================
def choose_duration_min(result, ai=None):
    if isinstance(ai, dict):
        try:
            d = int(ai.get("duration_min", 0))
            if d in (1, 2, 3, 4, 5, 10, 15):
                return d
        except Exception:
            pass
    adx = float(result.get("adx", 0))
    rsi = float(result.get("rsi", 50))
    if adx >= 35:
        return 10 if (rsi >= 55 or rsi <= 45) else 5
    if adx >= 25:
        return 5
    if adx >= 20:
        return 4
    if adx >= 15:
        return 3
    return 1

def ai_prompt(result):
    return f"""You are a cautious OlympTrade market-setup validator. Do not invent data.
Evaluate price action, candle patterns, market structure, EMA, MACD, RSI, Bollinger Bands,
Stochastic, ADX, support/resistance and conflicts.
Rules:
- ADX below 15 is strong caution; 15-20 reduces confidence but is not automatic rejection.
- Conflicting evidence => REJECT / NO SIGNAL.
- One candle pattern alone is never sufficient.
- Approve only a coherent, aligned setup.
- Confidence is a validation score, NOT a guaranteed win probability.
- If approving, choose the most suitable expiry from exactly: 1, 2, 3, 4, 5, 10, 15 minutes.
- Choose expiry from setup quality, momentum, volatility, candle structure, trend strength and support/resistance distance.
- Do not choose a longer expiry merely to avoid NO SIGNAL.
- If the setup is conflicted or unsafe, reject it.
Return JSON only:
{{"decision":"APPROVE|REJECT","direction":"UP|DOWN|NO SIGNAL","confidence":0-100,"duration_min":1|2|3|4|5|10|15,"reason":"short reason"}}
DATA:
{json.dumps(result, ensure_ascii=False)}""".strip()

def parse_ai(data):
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices: raise ValueError(f"AI response missing choices: {str(data)[:400]}")
    msg = choices[0].get("message", {})
    content = msg.get("content") or msg.get("reasoning") or choices[0].get("text")
    if isinstance(content, list):
        content = "".join(str(x.get("text", x.get("content", ""))) if isinstance(x, dict) else str(x) for x in content)
    content = str(content or "").strip()
    content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.I)
    content = re.sub(r"\s*```$", "", content)
    if not content.startswith("{"):
        m = re.search(r"\{.*\}", content, re.S)
        if m: content = m.group(0)
    return json.loads(content)

def call_ai(prompt):
    providers = []
    if OPENROUTER_API_KEY:
        models = []
        for m in (OPENROUTER_MODEL, "openrouter/free", "minimax/minimax-m3:free", "google/gemma-4-26b-a4b-it:free"):
            if m and m not in models: models.append(m)
        for m in models:
            providers.append((f"OpenRouter/{m}", "https://openrouter.ai/api/v1/chat/completions", OPENROUTER_API_KEY, m))
    if AIRFORCE_API_KEY:
        providers.append(("Airforce", "https://api.airforce/v1/chat/completions", AIRFORCE_API_KEY, AIRFORCE_MODEL))
    if not providers: return None, "No AI provider configured"
    last_error = None
    for name, url, key, model in providers:
        try:
            headers = {"Authorization": f"Bearer {key.strip()}", "Content-Type": "application/json"}
            if name.startswith("OpenRouter/"):
                headers["HTTP-Referer"] = "https://priyanithan-ai.onrender.com"
                headers["X-Title"] = "Priyanithan AI OlympTrade Signal Bot"
            payload = {"model": model, "messages": [{"role":"system","content":"Return JSON only."},{"role":"user","content":prompt}], "temperature":0, "max_tokens":300}
            r = requests.post(url, headers=headers, json=payload, timeout=15)
            if not r.ok: raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500].replace(chr(10),' ')}")
            parsed = parse_ai(r.json())
            log.info("AI VALIDATION OK: provider=%s model=%s", name, model)
            return parsed, None
        except Exception as e:
            last_error = f"{name}: {e}"
            log.warning("AI provider failed: %s", last_error)
    return None, last_error or "AI failed"

# ============================================================
# TELEGRAM FORMAT
# ============================================================
def format_signal(result, ai=None):
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
                f"⚠️ {ai.get('reason','AI rejected the setup.')}\n\n"
                "⏳ Waiting for stronger confirmation...")
    fire = "🔥🔥🔥" if conf >= 94 else ("🔥🔥" if conf >= 91 else "🔥")
    arrow = "⬆️" if direction == "UP" else "⬇️"
    duration = f"{choose_duration_min(result, ai)} MIN"
    confirmations = "\n".join(f"• {x}" for x in result["reasons"][-7:])
    return (f"📈 {result['pair']}\n{arrow} TRADE {direction}\n🕐 {result['candle_time']}\n{fire}\n\n"
            f"{confirmations}\n\n⏱ Duration: {duration}\n🤖 AI: APPROVED\n📊 Confidence: {conf}%\n\n"
            "⚠️ Manual execution only")

# ============================================================
# TELEGRAM COMMANDS
# ============================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remember_chat(update)
    await update.message.reply_text("🤖 Priyanithan AI Bot\n\nUse /access YOUR_CODE\nThen /status or /signal ASIA_X")

async def access_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remember_chat(update)
    if not context.args:
        await update.message.reply_text("Use /access YOUR_CODE")
        return
    if ACCESS_CODE and context.args[0] == ACCESS_CODE:
        authorized_users.add(update.effective_user.id)
        await update.message.reply_text("✅ Access authorized. Automatic 5-minute scans are now enabled for this chat.")
    else:
        await update.message.reply_text("❌ Invalid access code.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remember_chat(update)
    if not is_authorized(update):
        await update.message.reply_text("🔒 Access required. Use /access YOUR_CODE")
        return
    connected = bool(ot_client and ot_client.connection.is_connected)
    ai = "OpenRouter" if OPENROUTER_API_KEY else ("Airforce" if AIRFORCE_API_KEY else "NOT CONFIGURED")
    await update.message.reply_text(
        "🟢 BOT STATUS\n\n"
        f"📡 OlympTrade Live Feed: {'CONNECTED' if connected else 'DISCONNECTED'}\n"
        f"📊 Pairs: {', '.join(PAIRS)}\n"
        f"🤖 AI: {ai}\n🧠 Model: {OPENROUTER_MODEL if OPENROUTER_API_KEY else AIRFORCE_MODEL}\n"
        f"🕐 Timezone: UAE (Asia/Dubai)\n⏱ Scan: every 5 min (:00/:05/:10...)\n\n"
        "⚡ Auto-trade: OFF\n🛑 Martingale: OFF\n👤 Execution: MANUAL ONLY\n📡 Source: OlympTrade"
    )

async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remember_chat(update)
    if not is_authorized(update):
        await update.message.reply_text("🔒 Access required. Use /access YOUR_CODE")
        return
    pair = context.args[0].upper() if context.args else "ASIA_X"
    if pair not in PAIRS:
        await update.message.reply_text(f"❌ Unsupported pair. Available: {', '.join(PAIRS)}")
        return
    msg = await update.message.reply_text(f"⏳ Reading OlympTrade live candles for {pair}...")
    df, err = await get_ot_candles(pair, 60, 260)
    if err:
        await msg.edit_text(f"❌ LIVE DATA FAILED\n\n{err}")
        return
    result = analyze(df, pair)
    latest_candles[pair], latest_signal[pair] = df, result
    if result["signal"] == "NO SIGNAL":
        await msg.edit_text(format_signal(result))
        return
    await msg.edit_text("🤖 Strong setup found. AI validation running...")
    ai, ai_err = await asyncio.to_thread(call_ai, ai_prompt(result))
    if ai_err:
        await msg.edit_text(f"🚫 NO SIGNAL\n\n📈 {pair}\n❌ AI validation unavailable: {ai_err}")
        return
    log.info("AI DECISION DETAIL MANUAL: pair=%s decision=%s direction=%s confidence=%s reason=%s", pair, ai.get("decision"), ai.get("direction"), ai.get("confidence"), str(ai.get("reason",""))[:500])
    await msg.edit_text(format_signal(result, ai))

# ============================================================
# AUTOMATIC 5-MINUTE SCANNER
# ============================================================
async def scan_loop(application):
    # First scan is aligned to UAE clock, not arbitrary server/UTC time.
    await wait_until_next_5min_uae()
    while True:
        cycle_started = time.time()
        scan_time = now_uae().strftime("%H:%M:%S")
        log.info("AUTO SCAN START: UAE=%s pairs=%s recipients=%s", scan_time, PAIRS, recipients())
        try:
            for pair in PAIRS:
                df, err = await get_ot_candles(pair, 60, 260)
                if err:
                    await send_to_recipients(application.bot, f"⚠️ 5-MINUTE SCAN\n\n📈 {pair}\n❌ LIVE DATA UNAVAILABLE\n\n{err}")
                    continue
                result = analyze(df, pair)
                latest_candles[pair], latest_signal[pair] = df, result
                if result["signal"] == "NO SIGNAL":
                    await send_to_recipients(application.bot,
                        f"🚫 NO SIGNAL\n\n📈 {pair}\n🕐 {result['candle_time']}\n\n"
                        f"📊 Technical analysis: NO SIGNAL\n🧭 {result['reason']}\n"
                        f"📈 Trend: {result['trend']}\n📊 RSI: {result['rsi']:.1f}\n💪 ADX: {result['adx']:.1f}\n\n"
                        "⏳ Waiting for stronger setup...")
                    continue
                log.info("5-minute AI START: pair=%s", pair)
                try:
                    ai, ai_err = await asyncio.wait_for(asyncio.to_thread(call_ai, ai_prompt(result)), timeout=90)
                except asyncio.TimeoutError:
                    ai, ai_err = None, "AI validation timed out after 90 seconds"
                log.info("5-minute AI END: pair=%s error=%s", pair, ai_err)
                if ai_err or not ai:
                    await send_to_recipients(application.bot, f"⚠️ AI VALIDATION UNAVAILABLE\n\n📈 {pair}\n📊 Technical: {result['signal']} {result['confidence']}%\n❌ {ai_err or 'No AI response'}\n\n🚫 No trade signal generated.")
                    continue
                direction = str(ai.get("direction", "NO SIGNAL")).upper()
                decision = str(ai.get("decision", "REJECT")).upper()
                try: conf = int(ai.get("confidence", 0))
                except: conf = 0
                log.info("AI DECISION DETAIL: pair=%s decision=%s direction=%s confidence=%s reason=%s", pair, decision, direction, conf, str(ai.get("reason",""))[:500])
                approved = decision == "APPROVE" and direction in ("UP","DOWN") and direction == result["signal"] and conf >= AI_MIN_CONFIDENCE
                text = format_signal(result, ai) if approved else (
                    f"🚫 NO SIGNAL\n\n📈 {pair}\n🕐 {result['candle_time']}\n\n"
                    f"📊 Technical candidate: {result['signal']} ({result['confidence']}%)\n"
                    f"🤖 AI: {decision} / {direction} / {conf}%\n"
                    f"⚠️ {ai.get('reason','AI rejected the setup.')}\n\n⏳ Waiting for stronger confirmation...")
                await send_to_recipients(application.bot, text)
                log.info("5-minute scan completed: %s signal=%s confidence=%s", pair, result["signal"], result["confidence"])
        except Exception:
            log.exception("Scanner loop error")
        # Align again to the next exact UAE 5-minute boundary.
        elapsed = time.time() - cycle_started
        await wait_until_next_5min_uae()

# ============================================================
# MANUAL TRADE MONITOR - READ ONLY
# ============================================================
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
# MAIN
# ============================================================
def run_flask():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

async def telegram_runtime(application):
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
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if application.updater and application.updater.running: await application.updater.stop()
        if application.running: await application.stop()
        await application.shutdown()

def main():
    if not TELEGRAM_BOT_TOKEN: raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
    if not ACCESS_CODE: raise RuntimeError("ACCESS_CODE is missing")
    if not OLYMPTRADE_ACCESS_TOKEN: raise RuntimeError("OLYMPTRADE_ACCESS_TOKEN is missing")
    threading.Thread(target=run_flask, daemon=True).start()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("access", access_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("signal", signal_command))
    asyncio.run(telegram_runtime(application))

if __name__ == "__main__":
    main()

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from flask import Flask, request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from olymptrade_ws import OlympTradeClient
from olymptrade_ws.olympconfig import parameters

# ============================================================
# PRIYANITHAN — FRESH MAIN ENGINE
# Direct OlympTrade candles -> technical filter -> AI -> Telegram.
# Manual trading only. No broker order is ever submitted here.
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ACCESS_CODE = os.getenv("ACCESS_CODE", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
OLYMPTRADE_ACCESS_TOKEN = os.getenv("OLYMPTRADE_ACCESS_TOKEN", "").strip()

AI_MIN_CONFIDENCE = int(os.getenv("AI_MIN_CONFIDENCE", "72"))
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free").strip()
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "").strip()
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()

PAIR_ENV = os.getenv("OLYMP_PAIRS", "AUTO").strip()
ENV_PAIRS = [x.strip().upper() for x in PAIR_ENV.split(",") if x.strip() and x.strip().upper() != "AUTO"]

# Used only as discovery candidates. Every asset must pass a fresh OlympTrade candle check.
SEED_PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "EURJPY", "GBPJPY", "EURGBP", "AUDJPY", "CADJPY", "CHFJPY",
    "EURUSD_OTC", "GBPUSD_OTC", "USDJPY_OTC", "USDCHF_OTC", "USDCAD_OTC",
    "AUDUSD_OTC", "NZDUSD_OTC", "EURJPY_OTC", "GBPJPY_OTC", "AUDJPY_OTC",
    "CADJPY_OTC", "XAUUSD_OTC", "XAGUSD_OTC", "BTCUSD_OTC", "ASIA_X",
]

ALLOWED_DURATIONS = (2, 3, 5, 10, 15)
UAE_TZ = ZoneInfo("Asia/Dubai")
LIVE_1M_MAX_AGE = 90.0
LIVE_5M_MAX_AGE = 360.0
ASSET_PAGE_SIZE = 10

AUTO_TRADE = False
MARTINGALE = False

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("priyanithan")
APP_VERSION = "7.0-FRESH-LIVE-ASSET"
app = Flask(__name__)

# Runtime state.
ot_client = None
runtime_loop = None
telegram_application = None
authorized_users = set()
selected_asset = {}          # telegram user id -> pair
active_signal = {}           # telegram user id -> signal metadata
asset_cache = {}             # pair -> last successful validation
broker_catalog = set()
state_lock = threading.Lock()


def now_uae():
    return datetime.now(UAE_TZ)


def fmt_price(value):
    try:
        return f"{float(value):.6f}".rstrip("0").rstrip(".")
    except Exception:
        return str(value)


def fmt_ts(ts):
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(UAE_TZ).strftime("%H:%M:%S UAE")
    except Exception:
        return now_uae().strftime("%H:%M:%S UAE")


def wait_seconds_to_next_5m():
    now = now_uae()
    target_minute = ((now.minute // 5) + 1) * 5
    if target_minute >= 60:
        target = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    else:
        target = now.replace(minute=target_minute, second=0, microsecond=0)
    return max(1.0, (target - now).total_seconds())


def recipients():
    ids = set(int(x) for x in authorized_users)
    if TELEGRAM_CHAT_ID:
        try:
            ids.add(int(TELEGRAM_CHAT_ID))
        except ValueError:
            pass
    return ids


async def send_text(bot, text, chat_id=None):
    ids = [chat_id] if chat_id is not None else list(recipients())
    sent = False
    for cid in ids:
        try:
            await bot.send_message(chat_id=int(cid), text=text)
            sent = True
        except Exception as exc:
            log.warning("Telegram send failed chat=%s: %s", cid, exc)
    return sent


@app.get("/")
def home():
    return f"Priyanithan {APP_VERSION} ONLINE — Telegram controls the live asset selection."


@app.get("/health")
def health():
    return "OK"


@app.get("/status")
def status():
    connected = bool(ot_client and getattr(getattr(ot_client, "connection", None), "is_connected", False))
    return {
        "version": APP_VERSION,
        "olymptrade_connected": connected,
        "live_assets_cached": len(asset_cache),
        "selected_users": len(selected_asset),
        "auto_trade": False,
        "martingale": False,
    }


@app.post("/telegram/webhook")
def telegram_webhook():
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
    except Exception as exc:
        log.exception("Telegram webhook failed: %s", exc)
        return "Webhook processing failed", 500


def normalize_candles(raw):
    if isinstance(raw, dict):
        data = raw.get("d", raw)
        if isinstance(data, list) and data and isinstance(data[0], dict):
            data = data[0].get("candles", data)
        raw = data
    if not isinstance(raw, list):
        return None
    rows = []
    for candle in raw:
        if not isinstance(candle, dict):
            continue
        try:
            ts = candle.get("timestamp", candle.get("t", candle.get("time")))
            row = {
                "timestamp": float(ts) if ts is not None else time.time(),
                "open": float(candle.get("open", candle.get("o"))),
                "high": float(candle.get("high", candle.get("h"))),
                "low": float(candle.get("low", candle.get("l"))),
                "close": float(candle.get("close", candle.get("c"))),
            }
            rows.append(row)
        except (TypeError, ValueError):
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    if float(df["timestamp"].iloc[-1]) > 100_000_000_000:
        df["timestamp"] = df["timestamp"] / 1000.0
    return df


async def on_tick(message):
    # Keep this callback intentionally lightweight. Candle requests are the source of truth.
    return None


def extract_pairs(message):
    data = message.get("d") if isinstance(message, dict) else None
    if isinstance(data, dict):
        data = data.get("instruments", data.get("pairs", data.get("items", data.get("assets", []))))
    found = set()
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                pair = str(item.get("pair") or item.get("symbol") or item.get("name") or "").strip().upper()
                if pair:
                    found.add(pair)
    return found


async def on_instruments(message):
    found = extract_pairs(message)
    if found:
        broker_catalog.update(found)
        log.info("LIVE BROKER CATALOG: received %s instruments; total=%s", len(found), len(broker_catalog))


async def connect_olymptrade():
    global ot_client
    if not OLYMPTRADE_ACCESS_TOKEN:
        log.error("OLYMPTRADE_ACCESS_TOKEN is missing")
        return
    while True:
        try:
            client = OlympTradeClient(
                access_token=OLYMPTRADE_ACCESS_TOKEN,
                log_raw_messages=False,
            )
            client.register_callback(parameters.E_TICK_UPDATE, on_tick)
            client.register_callback(1054, on_instruments)
            await client.start()
            ot_client = client
            log.warning("OLYMPTRADE LIVE CONNECTION: CONNECTED")
            while client.connection.is_connected:
                await asyncio.sleep(5)
            raise ConnectionError("OlympTrade websocket disconnected")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            ot_client = None
            log.exception("OLYMPTRADE CONNECTION ERROR: %s", exc)
            await asyncio.sleep(5)


async def get_candles(pair, timeframe, count, max_age):
    client = ot_client
    if client is None or not client.connection.is_connected:
        return None, "OlympTrade not connected"
    try:
        raw = await client.market.get_candles(pair, timeframe, count)
    except Exception as exc:
        return None, f"request failed: {exc}"
    df = normalize_candles(raw)
    if df is None or len(df) < 40:
        return None, f"insufficient candles ({0 if df is None else len(df)})"
    latest = float(df["timestamp"].iloc[-1])
    age = time.time() - latest
    if latest > time.time() + 120:
        return None, f"future candle rejected age={age:.1f}s"
    if age > max_age:
        return None, f"STALE candle rejected age={age:.1f}s"
    return df, None


async def verify_live_pair(pair):
    df, err = await get_candles(pair, 60, 80, LIVE_1M_MAX_AGE)
    if df is None:
        return False, err
    latest = float(df["timestamp"].iloc[-1])
    asset_cache[pair] = {"ts": latest, "checked": time.time()}
    return True, None


async def discover_live_assets():
    # Prefer broker catalogue; use configured/common candidates only when catalogue is empty.
    candidates = set(broker_catalog)
    candidates.update(ENV_PAIRS)
    if not candidates:
        candidates.update(SEED_PAIRS)
    candidates = sorted(candidates)
    sem = asyncio.Semaphore(10)

    async def check(pair):
        async with sem:
            ok, err = await verify_live_pair(pair)
            return pair if ok else None

    results = await asyncio.gather(*(check(pair) for pair in candidates), return_exceptions=True)
    live = sorted({r for r in results if isinstance(r, str)})
    log.warning(
        "LIVE ASSET DISCOVERY: candidates=%s live=%s rejected=%s",
        len(candidates), len(live), len(candidates) - len(live),
    )
    return live


def asset_keyboard(live, page=0):
    start = page * ASSET_PAGE_SIZE
    chunk = live[start:start + ASSET_PAGE_SIZE]
    rows = []
    for pair in chunk:
        rows.append([InlineKeyboardButton(f"🟢 {pair}", callback_data=f"asset:{pair}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ PREV", callback_data=f"assets:{page - 1}"))
    if start + ASSET_PAGE_SIZE < len(live):
        nav.append(InlineKeyboardButton("NEXT ▶️", callback_data=f"assets:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔄 REFRESH LIVE ASSETS", callback_data="assets:refresh")])
    return InlineKeyboardMarkup(rows)


async def send_asset_menu(bot, user_id, note=None):
    live = await discover_live_assets()
    if not live:
        text = (
            "⚠️ NO LIVE ASSETS AVAILABLE\n\n"
            "OlympTrade returned no fresh 1-minute candle.\n"
            "Closed/stale assets are never shown.\n\n"
            "Tap REFRESH after the broker connection is live."
        )
        if note:
            text = note + "\n\n" + text
        await send_text(bot, text, user_id)
        return False
    text = (
        "📊 LIVE ASSET SELECTION\n\n"
        "Choose one asset below.\n"
        "🟢 = fresh OlympTrade candle verified\n"
        "⏱️ Signal cycle = every 5 minutes\n"
        "⏱️ AI duration = 2 / 3 / 5 / 10 / 15 MIN\n"
        "⚠️ Manual trade only — AUTO TRADE OFF"
    )
    if note:
        text = note + "\n\n" + text
    await bot.send_message(chat_id=user_id, text=text, reply_markup=asset_keyboard(live, 0))
    return True


async def access_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    if not ACCESS_CODE:
        await update.message.reply_text("❌ ACCESS_CODE is not configured on Render.")
        return
    args = context.args or []
    if not args or args[0].strip() != ACCESS_CODE:
        await update.message.reply_text("❌ Invalid access code.")
        return
    authorized_users.add(int(user.id))
    await update.message.reply_text("✅ ACCESS VERIFIED\n\n📊 LIVE ASSET SELECTION")
    await send_asset_menu(context.bot, int(user.id))


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    if int(user.id) not in authorized_users:
        await update.message.reply_text("Use /access YOUR_ACCESS_CODE first.")
        return
    await send_asset_menu(context.bot, int(user.id))


async def assets_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or int(user.id) not in authorized_users:
        await update.message.reply_text("❌ Access required. Use /access YOUR_ACCESS_CODE first.")
        return
    await send_asset_menu(context.bot, int(user.id))


async def assets_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    uid = int(query.from_user.id)
    if uid not in authorized_users:
        await query.message.reply_text("❌ Access required.")
        return

    data = query.data or ""
    if data == "assets:refresh":
        await query.edit_message_text("🔄 Checking fresh OlympTrade candles...")
        live = await discover_live_assets()
        if not live:
            await query.edit_message_text(
                "⚠️ NO LIVE ASSETS AVAILABLE\n\n"
                "OlympTrade returned no fresh 1-minute candle.\n"
                "Tap /assets again when the broker connection is live.",
                reply_markup=asset_keyboard([], 0),
            )
            return
        await query.edit_message_text(
            "📊 LIVE ASSET SELECTION\n\n🟢 Fresh OlympTrade candle verified. Choose an asset:",
            reply_markup=asset_keyboard(live, 0),
        )
        return

    if data.startswith("assets:"):
        try:
            page = int(data.split(":", 1)[1])
        except ValueError:
            page = 0
        live = await discover_live_assets()
        await query.edit_message_text(
            "📊 LIVE ASSET SELECTION\n\n🟢 Fresh OlympTrade candle verified. Choose an asset:",
            reply_markup=asset_keyboard(live, page),
        )
        return

    if not data.startswith("asset:"):
        return
    pair = data.split(":", 1)[1].strip().upper()
    ok, err = await verify_live_pair(pair)
    if not ok:
        await query.edit_message_text(
            f"❌ {pair} is no longer live.\n\n{err}\n\nRefresh and choose another asset."
        )
        return

    selected_asset[uid] = pair
    active_signal.pop(uid, None)
    next_in = int(round(wait_seconds_to_next_5m()))
    await query.edit_message_text(
        f"✅ ASSET SELECTED: {pair}\n\n"
        "🟢 LIVE CANDLE VERIFIED\n"
        "🤖 AI analysis: ON\n"
        "⏱️ Signal cycle: every 5 minutes\n"
        "📊 AI duration: 2 / 3 / 5 / 10 / 15 MIN\n"
        f"⏳ Next signal window in ~{next_in}s\n\n"
        "⚠️ MANUAL TRADE ONLY — AUTO TRADE OFF"
    )
    log.warning("USER ASSET SELECTED: user=%s pair=%s", uid, pair)


def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))


def technical_analysis(pair, df1, df5):
    c = df1["close"]
    e9 = ema(c, 9).iloc[-1]
    e21 = ema(c, 21).iloc[-1]
    e50 = ema(c, 50).iloc[-1]
    macd = ema(c, 12) - ema(c, 26)
    macd_sig = ema(macd, 9)
    rv = float(rsi(c).iloc[-1])
    last = float(c.iloc[-1])
    prev = float(c.iloc[-2])
    body = abs(last - float(df1["open"].iloc[-1]))
    rng = max(1e-12, float(df1["high"].iloc[-1]) - float(df1["low"].iloc[-1]))
    body_ratio = body / rng

    h5 = df5["close"]
    h5e9 = ema(h5, 9).iloc[-1]
    h5e21 = ema(h5, 21).iloc[-1]
    h5dir = "UP" if h5e9 > h5e21 else "DOWN" if h5e9 < h5e21 else "FLAT"

    up = 0
    down = 0
    reasons = []
    if e9 > e21:
        up += 2; reasons.append("1m EMA9>EMA21")
    elif e9 < e21:
        down += 2; reasons.append("1m EMA9<EMA21")
    if e21 > e50:
        up += 1
    elif e21 < e50:
        down += 1
    if macd.iloc[-1] > macd_sig.iloc[-1]:
        up += 2; reasons.append("MACD bullish")
    else:
        down += 2; reasons.append("MACD bearish")
    if 50 <= rv <= 68:
        up += 1
    elif 32 <= rv < 50:
        down += 1
    if last > prev:
        up += 1
    elif last < prev:
        down += 1
    if h5dir == "UP":
        up += 2; reasons.append("5m trend UP")
    elif h5dir == "DOWN":
        down += 2; reasons.append("5m trend DOWN")

    direction = "UP" if up > down else "DOWN" if down > up else "NONE"
    score = max(up, down)
    confidence = int(min(95, 50 + score * 6 + (5 if body_ratio >= 0.55 else 0)))
    return {
        "pair": pair,
        "direction": direction,
        "confidence": confidence,
        "price": last,
        "rsi": round(rv, 2),
        "body_ratio": round(body_ratio, 2),
        "trend_5m": h5dir,
        "up_votes": up,
        "down_votes": down,
        "reasons": reasons,
        "candle_time": fmt_ts(float(df1["timestamp"].iloc[-1])),
    }


def ai_request(prompt):
    providers = []
    if OPENROUTER_API_KEY:
        providers.append(("OpenRouter", "https://openrouter.ai/api/v1/chat/completions", OPENROUTER_API_KEY, OPENROUTER_MODEL))
    if CEREBRAS_API_KEY:
        providers.append(("Cerebras", "https://api.cerebras.ai/v1/chat/completions", CEREBRAS_API_KEY, CEREBRAS_MODEL))
    if GROQ_API_KEY:
        providers.append(("Groq", "https://api.groq.com/openai/v1/chat/completions", GROQ_API_KEY, GROQ_MODEL))
    if not providers:
        return None, "No AI provider configured"

    system = (
        "You are Candice, a disciplined professional market analyst. "
        "Use ONLY the supplied live candle/indicator snapshot. Do not invent price data. "
        "Approve only when direction is technically coherent with the 5m trend and the 1m structure. "
        "Return JSON only: decision APPROVE/REJECT, direction UP/DOWN, confidence 0-100, "
        "duration_min one of 2,3,5,10,15, reason short. Never return 1 minute."
    )
    body = {
        "model": None,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 300,
    }
    for name, url, key, model in providers:
        body["model"] = model
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=body,
                timeout=30,
            )
            if r.status_code >= 400:
                log.warning("AI %s HTTP %s", name, r.status_code)
                continue
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
            content = str(content).strip().replace("```json", "").replace("```", "").strip()
            result = json.loads(content)
            return result, name
        except Exception as exc:
            log.warning("AI %s failed: %s", name, exc)
    return None, "All AI providers failed"


async def analyze_selected(uid, pair):
    df1, err1 = await get_candles(pair, 60, 100, LIVE_1M_MAX_AGE)
    if df1 is None:
        log.warning("SELECTED SCAN REJECTED: pair=%s reason=%s", pair, err1)
        return None
    df5, err5 = await get_candles(pair, 300, 80, LIVE_5M_MAX_AGE)
    if df5 is None:
        log.warning("SELECTED SCAN REJECTED: pair=%s 5m=%s", pair, err5)
        return None
    tech = technical_analysis(pair, df1, df5)
    if tech["direction"] == "NONE" or tech["confidence"] < 62:
        log.info("SELECTED TECHNICAL REJECT: pair=%s direction=%s confidence=%s", pair, tech["direction"], tech["confidence"])
        return None

    prompt = json.dumps({
        "market": "OlympTrade live candles",
        "pair": pair,
        "entry_price": tech["price"],
        "1m_candle_time": tech["candle_time"],
        "1m_direction": tech["direction"],
        "technical_confidence": tech["confidence"],
        "5m_trend": tech["trend_5m"],
        "rsi": tech["rsi"],
        "body_ratio": tech["body_ratio"],
        "up_votes": tech["up_votes"],
        "down_votes": tech["down_votes"],
        "reasons": tech["reasons"],
    })
    ai, provider = await asyncio.to_thread(ai_request, prompt)
    if not isinstance(ai, dict):
        log.warning("AI REJECT: pair=%s provider=%s", pair, provider)
        return None
    decision = str(ai.get("decision", "")).upper()
    direction = str(ai.get("direction", "")).upper()
    try:
        confidence = int(ai.get("confidence", 0))
        duration = int(ai.get("duration_min", 5))
    except (TypeError, ValueError):
        return None
    if decision != "APPROVE" or direction != tech["direction"] or confidence < AI_MIN_CONFIDENCE:
        log.info("AI GATE REJECT: pair=%s decision=%s direction=%s confidence=%s", pair, decision, direction, confidence)
        return None
    if duration not in ALLOWED_DURATIONS:
        log.info("AI GATE REJECT: pair=%s invalid_duration=%s", pair, duration)
        return None
    return {
        **tech,
        "ai_confidence": confidence,
        "duration": duration,
        "ai_reason": str(ai.get("reason", "Candice AI approved live structure"))[:220],
        "provider": provider,
        "created_at": time.time(),
    }


async def send_signal(bot, uid, signal):
    arrow = "⬆️ UP" if signal["direction"] == "UP" else "⬇️ DOWN"
    text = (
        "🔥 PRIYANITHAN AI SIGNAL 🔥\n\n"
        f"📈 {signal['pair']}\n"
        f"{arrow}\n"
        f"💰 Entry: {fmt_price(signal['price'])}\n"
        f"⏱️ Duration: {signal['duration']} MIN\n"
        f"🤖 Candice AI: APPROVED ({signal['ai_confidence']}%)\n"
        f"📊 Technical: {signal['confidence']}%\n"
        f"🕯️ 5m Trend: {signal['trend_5m']}\n"
        f"🕐 Candle: {signal['candle_time']}\n"
        f"🧠 {signal['ai_reason']}\n\n"
        "⚠️ MANUAL TRADE — AUTO TRADE OFF"
    )
    await send_text(bot, text, uid)


async def monitor_result(bot, uid, signal):
    await asyncio.sleep(signal["duration"] * 60)
    pair = signal["pair"]
    df, err = await get_candles(pair, 60, 20, 120)
    if df is None:
        await send_text(bot, f"⚠️ RESULT UNRESOLVED — {pair}\nFresh expiry candle unavailable: {err}", uid)
        active_signal.pop(uid, None)
        return
    expiry = float(df["close"].iloc[-1])
    entry = float(signal["price"])
    direction = signal["direction"]
    if expiry == entry:
        result = "DRAW"
    elif (direction == "UP" and expiry > entry) or (direction == "DOWN" and expiry < entry):
        result = "WIN"
    else:
        result = "LOSS"
    await send_text(
        bot,
        f"📊 TRADE RESULT\n\n"
        f"📈 {pair}\n"
        f"{'⬆️' if direction == 'UP' else '⬇️'} {direction}\n"
        f"💰 Entry: {fmt_price(entry)}\n"
        f"🏁 Expiry: {fmt_price(expiry)}\n"
        f"⏱️ Duration: {signal['duration']} MIN\n\n"
        f"{'✅' if result == 'WIN' else '❌' if result == 'LOSS' else '➖'} {result}",
        uid,
    )
    active_signal.pop(uid, None)
    if result == "LOSS":
        selected_asset.pop(uid, None)
        await send_asset_menu(bot, uid, "🔁 LOSS → AI will re-check live assets. Choose the next asset.")


async def selected_cycle(application):
    while True:
        await asyncio.sleep(wait_seconds_to_next_5m())
        users = list(selected_asset.items())
        log.warning("5-MINUTE SELECTED CYCLE: users=%s", len(users))
        for uid, pair in users:
            if uid in active_signal:
                log.info("CYCLE SKIP: user=%s pair=%s result still pending", uid, pair)
                continue
            try:
                live, err = await verify_live_pair(pair)
                if not live:
                    await send_text(application.bot, f"⚠️ {pair} is no longer live. Selecting a new live asset.", uid)
                    selected_asset.pop(uid, None)
                    await send_asset_menu(application.bot, uid)
                    continue
                signal = await analyze_selected(uid, pair)
                if signal is None:
                    log.info("NO QUALIFIED SIGNAL: selected pair=%s user=%s", pair, uid)
                    await send_text(application.bot, f"🔎 {pair}\nNo qualified AI setup this 5-minute window. Next window will re-check live candles.", uid)
                    continue
                active_signal[uid] = signal
                await send_signal(application.bot, uid, signal)
                asyncio.create_task(monitor_result(application.bot, uid, signal))
            except Exception as exc:
                log.exception("Selected cycle error user=%s pair=%s: %s", uid, pair, exc)


def start_flask():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)


async def telegram_runtime(application):
    global runtime_loop, telegram_application
    runtime_loop = asyncio.get_running_loop()
    telegram_application = application
    await application.initialize()
    await application.start()

    tasks = [
        asyncio.create_task(connect_olymptrade(), name="olymptrade-live"),
        asyncio.create_task(selected_cycle(application), name="selected-5m-cycle"),
    ]
    webhook_base = os.getenv("RENDER_EXTERNAL_URL", "https://priyanithan.onrender.com").rstrip("/")
    webhook_url = f"{webhook_base}/telegram/webhook"
    await application.bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )
    log.warning("TELEGRAM WEBHOOK ACTIVE: %s", webhook_url)
    log.warning("FINAL MODE: live asset buttons -> selected pair -> exact 5-minute cycle -> WIN/LOSS")
    log.warning("AUTO_TRADE=%s MARTINGALE=%s DURATIONS=%s", AUTO_TRADE, MARTINGALE, ALLOWED_DURATIONS)

    try:
        await asyncio.Event().wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await application.stop()
        await application.shutdown()


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    if not OLYMPTRADE_ACCESS_TOKEN:
        raise RuntimeError("OLYMPTRADE_ACCESS_TOKEN missing")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).updater(None).build()
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("access", access_cmd))
    application.add_handler(CommandHandler("assets", assets_cmd))
    application.add_handler(CallbackQueryHandler(assets_callback, pattern=r"^(asset:|assets:)"))

    threading.Thread(target=start_flask, name="flask", daemon=True).start()
    asyncio.run(telegram_runtime(application))


if __name__ == "__main__":
    main()

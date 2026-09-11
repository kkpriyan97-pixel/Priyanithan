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

ot_client = None
runtime_loop = None
telegram_application = None
authorized_users = set()
selected_asset = {}
active_signal = {}
asset_cache = {}
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


async def get_expiry_price(pair, expiry_ts):
    """Read the price at/just before the exact expiry boundary.

    This deliberately bypasses get_candles(), whose 40-candle requirement is
    appropriate for analysis but not for result verification. OlympTrade can
    validly return only 20 recent candles for this endpoint.
    """
    client = ot_client
    if client is None or not client.connection.is_connected:
        return None, None, "OlympTrade not connected"

    last_error = "no expiry candle"
    for attempt in range(8):
        try:
            raw = await client.market.get_candles(pair, 60, 20)
            df = normalize_candles(raw)
            if df is None or df.empty:
                last_error = "no valid expiry candles"
            else:
                rows = []
                for _, row in df.iterrows():
                    try:
                        ts = float(row["timestamp"])
                        close = float(row["close"])
                        if close > 0:
                            rows.append((ts, close))
                    except (TypeError, ValueError, KeyError):
                        continue
                rows.sort(key=lambda x: x[0])
                if rows:
                    # Prefer an exact boundary candle. If timestamps represent
                    # candle starts, accept the candle containing expiry.
                    exact = [x for x in rows if abs(x[0] - expiry_ts) <= 2.0]
                    if exact:
                        return exact[-1][1], exact[-1][0], "candle-exact"

                    containing = [x for x in rows if x[0] <= expiry_ts < x[0] + 60.0]
                    if containing:
                        return containing[-1][1], containing[-1][0], "candle-boundary"

                    closed = [x for x in rows if x[0] <= expiry_ts - 0.5]
                    if closed:
                        return closed[-1][1], closed[-1][0], "candle-closed"

                    last_error = "no candle at or before expiry boundary"
                else:
                    last_error = "no valid expiry candles"
        except Exception as exc:
            last_error = str(exc)
            log.warning("EXPIRY PRICE READ FAILED pair=%s attempt=%s: %s", pair, attempt + 1, exc)
        await asyncio.sleep(2.0)
    return None, None, last_error


def verify_result(entry, expiry, direction):
    if expiry == entry:
        return "DRAW"
    if direction == "UP":
        return "WIN" if expiry > entry else "LOSS"
    return "WIN" if expiry < entry else "LOSS"


def extract_pairs_for_menu(message):
    return extract_pairs(message)


def asset_keyboard(live, page=0):
    start = page * ASSET_PAGE_SIZE
    chunk = live[start:start + ASSET_PAGE_SIZE]
    rows = [[InlineKeyboardButton(f"🟢 {pair}", callback_data=f"asset:{pair}")] for pair in chunk]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ PREV", callback_data=f"assets:{page - 1}"))
    if start + ASSET_PAGE_SIZE < len(live):
        nav.append(InlineKeyboardButton("NEXT ▶️", callback_data=f"assets:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔄 REFRESH LIVE ASSETS", callback_data="assets:refresh")])
    return InlineKeyboardMarkup(rows)


async def verify_live_pair(pair):
    df, err = await get_candles(pair, 60, 80, LIVE_1M_MAX_AGE)
    if df is None:
        return False, err
    latest = float(df["timestamp"].iloc[-1])
    asset_cache[pair] = {"ts": latest, "checked": time.time()}
    return True, None


async def discover_live_assets():
    candidates = set(broker_catalog)
    candidates.update(ENV_PAIRS)
    if not candidates:
        candidates.update(SEED_PAIRS)
    candidates = sorted(candidates)
    sem = asyncio.Semaphore(10)

    async def check(pair):
        async with sem:
            ok, _ = await verify_live_pair(pair)
            return pair if ok else None

    results = await asyncio.gather(*(check(pair) for pair in candidates), return_exceptions=True)
    live = sorted({r for r in results if isinstance(r, str)})
    log.warning("LIVE ASSET DISCOVERY: candidates=%s live=%s rejected=%s", len(candidates), len(live), len(candidates) - len(live))
    return live


async def send_asset_menu(bot, user_id, note=None):
    live = await discover_live_assets()
    if not live:
        text = "⚠️ NO LIVE ASSETS AVAILABLE\n\nOlympTrade returned no fresh 1-minute candle.\nClosed/stale assets are never shown.\n\nTap REFRESH after the broker connection is live."
        if note:
            text = note + "\n\n" + text
        await send_text(bot, text, user_id)
        return False
    text = "📊 LIVE ASSET SELECTION\n\nChoose one asset below.\n🟢 = fresh OlympTrade candle verified\n⏱️ Next signal window = next 5 minutes\n⏱️ AI duration = 2 / 3 / 5 / 10 / 15 MIN\n⚠️ Manual trade only — AUTO TRADE OFF"
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
            await query.edit_message_text("⚠️ NO LIVE ASSETS AVAILABLE\n\nOlympTrade returned no fresh 1-minute candle.\nTap /assets again when the broker connection is live.", reply_markup=asset_keyboard([], 0))
            return
        await query.edit_message_text("📊 LIVE ASSET SELECTION\n\n🟢 Fresh OlympTrade candle verified. Choose an asset:\n⏱️ Next signal window = next 5 minutes\n⏱️ AI duration = 2 / 3 / 5 / 10 / 15 MIN\n⚠️ Manual trade only — AUTO TRADE OFF", reply_markup=asset_keyboard(live, 0))
        return
    if data.startswith("assets:"):
        try:
            page = int(data.split(":", 1)[1])
        except ValueError:
            page = 0
        live = await discover_live_assets()
        await query.edit_message_reply_markup(reply_markup=asset_keyboard(live, page))
        return
    if data.startswith("asset:"):
        pair = data.split(":", 1)[1].strip().upper()
        ok, err = await verify_live_pair(pair)
        if not ok:
            await query.message.reply_text(f"⚠️ {pair} is not currently live: {err}\n\nChoose another live asset.")
            return
        selected_asset[uid] = pair
        active_signal.pop(uid, None)
        await query.message.reply_text(
            f"✅ ASSET SELECTED — {pair}\n\n"
            "🟢 Fresh 1-minute candle verified\n"
            "🤖 Candice AI analysis ON\n"
            "⏱️ Next signal window = next 5 minutes\n"
            "⏱️ AI duration = 2 / 3 / 5 / 10 / 15 MIN\n"
            "⚠️ MANUAL TRADE ONLY — AUTO TRADE OFF"
        )


def technical_analysis(pair, df1, df5):
    close = df1["close"]
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    body = abs(float(df1["close"].iloc[-1]) - float(df1["open"].iloc[-1]))
    rng = max(1e-12, float(df1["high"].iloc[-1]) - float(df1["low"].iloc[-1]))
    body_ratio = body / rng
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    rs = gain / loss if loss and loss > 0 else 99
    rv = 100 - (100 / (1 + rs))
    h5dir = "UP" if float(df5["close"].iloc[-1]) > float(df5["close"].iloc[-2]) else "DOWN"
    up = 0
    down = 0
    reasons = []
    if last > prev:
        up += 1
    elif last < prev:
        down += 1
    if rv < 35:
        up += 1; reasons.append("RSI oversold")
    elif rv > 65:
        down += 1; reasons.append("RSI overbought")
    if body_ratio >= 0.55:
        if last > float(df1["open"].iloc[-1]):
            up += 1; reasons.append("strong bullish candle")
        elif last < float(df1["open"].iloc[-1]):
            down += 1; reasons.append("strong bearish candle")
    if h5dir == "UP":
        up += 1
    else:
        down += 1
    signal = "UP" if up > down else "DOWN" if down > up else "NO SIGNAL"
    confidence = int(min(95, 50 + abs(up - down) * 10))
    return {"pair": pair, "signal": signal, "confidence": confidence, "trend_5m": h5dir, "reason": "; ".join(reasons) or "mixed structure"}


def verify_signal_inputs(pair, df1, df5):
    if df1 is None or df5 is None:
        return False, "missing timeframe data"
    if len(df1) < 40 or len(df5) < 40:
        return False, "insufficient analysis candles"
    latest = float(df1["timestamp"].iloc[-1])
    age = time.time() - latest
    if age < -120 or age > LIVE_1M_MAX_AGE:
        return False, f"1m candle freshness failed age={age:.1f}s"
    return True, None


async def analyze_selected_asset(pair):
    df1, err1 = await get_candles(pair, 60, 80, LIVE_1M_MAX_AGE)
    if df1 is None:
        return None, err1
    df5, err5 = await get_candles(pair, 300, 80, LIVE_5M_MAX_AGE)
    if df5 is None:
        return None, err5
    ok, err = verify_signal_inputs(pair, df1, df5)
    if not ok:
        return None, err
    return technical_analysis(pair, df1, df5), None


async def scan_cycle(application):
    # Selected asset is verified here; the final signal engine may replace this
    # function at runtime while preserving the same 5-minute selected-asset UI.
    await asyncio.sleep(0)
    return None


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
    expiry_ts = float(signal.get("created_at", time.time())) + signal["duration"] * 60
    await asyncio.sleep(max(1.0, expiry_ts - time.time()))
    pair = signal["pair"]
    entry = float(signal["price"])
    direction = signal["direction"]
    expiry, expiry_price_ts, source = await get_expiry_price(pair, expiry_ts)
    if expiry is None:
        await send_text(bot, f"⚠️ RESULT UNRESOLVED — {pair}\nExpiry boundary price unavailable: {source}\nNo result was guessed.", uid)
        active_signal.pop(uid, None)
        return
    result = verify_result(entry, expiry, direction)
    await send_text(
        bot,
        "📊 TRADE RESULT\n\n"
        f"📈 {pair}\n"
        f"{'⬆️' if direction == 'UP' else '⬇️'} {direction}\n"
        f"💰 Entry: {fmt_price(entry)}\n"
        f"🏁 Expiry: {fmt_price(expiry)}\n"
        f"⏱️ Duration: {signal['duration']} MIN\n"
        f"🕐 Expiry boundary: {fmt_ts(expiry_ts)}\n"
        f"🔎 Verification: {source}\n"
        f"📌 Price candle: {fmt_ts(expiry_price_ts)}\n\n"
        f"{'✅' if result == 'WIN' else '❌' if result == 'LOSS' else '➖'} {result}\n\n"
        "⚠️ RESULT ONLY — AUTO TRADE OFF",
        uid,
    )
    active_signal.pop(uid, None)
    if result == "LOSS":
        selected_asset.pop(uid, None)
        await send_asset_menu(bot, uid, "🔁 LOSS → AI will re-check live assets. Choose the next asset.")


def build_application():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).updater(None).build()
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("access", access_cmd))
    application.add_handler(CommandHandler("assets", assets_cmd))
    application.add_handler(CallbackQueryHandler(assets_callback, pattern=r"^(asset:|assets:)"))
    return application


async def telegram_runtime(application):
    global runtime_loop, telegram_application
    telegram_application = application
    runtime_loop = asyncio.get_running_loop()
    await application.initialize()
    await application.start()
    webhook_url = os.getenv("RENDER_EXTERNAL_URL", "https://priyanithan.onrender.com").rstrip("/") + "/telegram/webhook"
    await application.bot.set_webhook(webhook_url)
    log.warning("TELEGRAM WEBHOOK ACTIVE: %s", webhook_url)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await application.stop()
        await application.shutdown()


async def main_async():
    application = build_application()
    loop = asyncio.get_running_loop()
    loop.create_task(connect_olymptrade())
    await asyncio.sleep(1)
    loop.create_task(telegram_runtime(application))
    while True:
        await asyncio.sleep(3600)


def run():
    asyncio.run(main_async())


if __name__ == "__main__":
    run()

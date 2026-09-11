import asyncio
import json
import logging
import os
import sys
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

# When Render executes `python app.py`, Python names this module `__main__`.
# Keep `app` as an alias to the same object so startup hotfixes cannot create a
# second app.py module with an isolated ot_client / Telegram state.
if __name__ == "__main__":
    sys.modules.setdefault("app", sys.modules[__name__])

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


def run_http_server():
    """Bind the Render-required HTTP port while the async trading engine runs."""
    try:
        port = int(os.getenv("PORT", "10000"))
    except ValueError:
        port = 10000
    log.warning("RENDER HTTP SERVER: binding 0.0.0.0:%s", port)
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)


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

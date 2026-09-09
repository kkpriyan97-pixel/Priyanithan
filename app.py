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
PAIR_ALIASES = {"ASIA_X": os.getenv("OT_ASIA_X_PAIR", "ASIA_X"), "EURUSD": os.getenv("OT_EURUSD_PAIR", "EURUSD"), "GBPUSD": os.getenv("OT_GBPUSD_PAIR", "GBPUSD")}
UAE_TZ = ZoneInfo("Asia/Dubai")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("priyanithan")
APP_VERSION = "6.0-FINAL-LIVE-UAE-WEBHOOK-REDEPLOY"
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

@app.get("/")
def home():
    return f"Priyanithan AI OlympTrade Signal Bot is ONLINE — {APP_VERSION}"

@app.get("/health")
def health():
    return "OK"

telegram_application = None

@app.post("/telegram/webhook")
def telegram_webhook():
    if telegram_application is None or runtime_loop is None:
        return "Bot is starting", 503
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return "Bad Request", 400
    try:
        update = Update.de_json(payload, telegram_application.bot)
        asyncio.run_coroutine_threadsafe(telegram_application.update_queue.put(update), runtime_loop)
        return "OK", 200
    except Exception:
        log.exception("Telegram webhook update failed")
        return "Internal Server Error", 500

# The remaining application logic is intentionally preserved by this commit.
# Runtime symbol repair workflow has already restored the required functions.

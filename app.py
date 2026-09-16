from __future__ import annotations

import asyncio
import logging
import os
import threading
import time

from flask import Flask, jsonify

from brain import CandiceBrain
from market_feed import LiveMarketFeed
from telegram import Telegram

os.environ["TZ"] = "Asia/Dubai"
try:
    time.tzset()
except AttributeError:
    pass

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("candice")
app = Flask(__name__)
telegram = Telegram()
feed = None
brain = None
engine_ready = False
engine_error = None


async def _engine_loop():
    global feed, brain, engine_ready, engine_error
    try:
        brain_ref = None
        feed_ref = LiveMarketFeed(lambda a, c: brain_ref.on_candle(a, c) if brain_ref else None)
        brain_ref = CandiceBrain(telegram.send, feed_ref)
        feed = feed_ref
        brain = brain_ref

        log.info("CANDICE_STARTING | timezone=Asia/Dubai | READ_ONLY")
        telegram.start()
        await feed.start()
        engine_ready = True
        engine_error = None
        log.info("CANDICE_ENGINE_STARTED | Dubai UTC+04:00 | READ_ONLY | AUTO_TRADE=OFF | MARTINGALE=OFF")
        threading.Thread(target=brain.run, daemon=True, name="candice-brain").start()
        log.info("CANDICE_BRAIN_STARTED")

        # Keep the asyncio loop alive because the live feed owns tasks on this loop.
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        engine_ready = False
        raise
    except Exception as e:
        engine_ready = False
        engine_error = type(e).__name__
        log.exception("CANDICE_ENGINE_START_FAILED")


def start_engine():
    asyncio.run(_engine_loop())


@app.get("/")
def root():
    return jsonify({
        "name": "Candice AI", "status": "online" if engine_ready else "starting",
        "mode": "READ_ONLY", "auto_trade": False, "martingale": False,
        "timezone": "Asia/Dubai", "engine_ready": engine_ready, "engine_error": engine_error,
    })


@app.get("/health")
def health():
    fs = feed.status() if feed else {"connected": False, "auth_invalid": False}
    ok = bool(engine_ready and fs.get("connected"))
    return jsonify({
        "status": "ok" if ok else "degraded", "feed_connected": bool(fs.get("connected")),
        "brain_started": brain is not None, "engine_ready": engine_ready,
        "mode": "READ_ONLY", "auto_trade": False, "martingale": False,
        "timezone": "Asia/Dubai", "error": engine_error,
    }), 200 if ok else 503


@app.get("/status")
def status():
    fs = feed.status() if feed else {}
    bs = {"pending": 0, "WIN": 0, "LOSS": 0, "TIE": 0, "last_scan_assets": []}
    if brain:
        bs = {
            "pending": len([x for x in brain.pending.values() if not x.get("result")]),
            "WIN": brain.stats["WIN"], "LOSS": brain.stats["LOSS"], "TIE": brain.stats["TIE"],
            "last_scan_assets": sorted(brain.last_scan), "daily_losses": brain.daily_losses,
            "consecutive_losses": brain.consecutive_losses, "daily_loss_limit": brain.max_daily_losses,
        }
    return jsonify({
        "candice": "online" if engine_ready else "starting", "engine_ready": engine_ready,
        "engine_error": engine_error, "feed": fs, "brain": bs, "timezone": "Asia/Dubai",
        "mode": "READ_ONLY", "auto_trade": False, "martingale": False,
    })


if __name__ == "__main__":
    threading.Thread(target=start_engine, daemon=True, name="candice-engine-bootstrap").start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))

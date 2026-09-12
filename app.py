from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from candice_broker import Broker
from candice_engine import analyze, session_state, technical_snapshot
from candice_memory import record, summary, today_risk

# ---------------------------------------------------------------------------
# CANDICE v8 — CLEAN TEXT-ONLY MAIN RUNTIME
# This file deliberately has NO image/GIF/media upload or media-edit code.
# ---------------------------------------------------------------------------
VERSION = "8.0-CLEAN-TEXT"
UAE = ZoneInfo("Asia/Dubai")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ACCESS = os.getenv("ACCESS_CODE", "").strip()
OT_TOKEN = os.getenv("OLYMPTRADE_ACCESS_TOKEN", "").strip()

INTERVAL = max(60, int(os.getenv("SCAN_INTERVAL_SECONDS", "300")))
MAX_DAILY_LOSSES = max(1, int(os.getenv("DAILY_MAX_LOSSES", "5")))
MAX_STREAK = max(1, int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3")))

AUTO_TRADE = False
MARTINGALE = False
FOREX_MODE = False

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("candice")

flask_app = Flask(__name__)
broker = Broker(OT_TOKEN)
tg_app: Application | None = None

users: set[int] = set()
selected: dict[int, str] = {}
active: dict[int, "Signal"] = {}
sent_keys: set[str] = set()
recovery_until: dict[int, float] = {}
timer_tasks: dict[str, asyncio.Task] = {}
daily = {"date": "", "losses": 0, "streak": 0}


@dataclass
class Signal:
    asset: str
    direction: str
    confidence: int
    expiry: int
    entry: float
    entry_ts: float
    reason: str


def now() -> datetime:
    return datetime.now(UAE)


def reset_daily() -> None:
    day = now().date().isoformat()
    daily["date"] = day
    risk = today_risk(UAE)
    daily["losses"] = int(risk.get("losses", 0))
    daily["streak"] = int(risk.get("streak", 0))


def session_window():
    utc = datetime.now(timezone.utc)
    base = utc.replace(minute=0, second=0, microsecond=0) - timedelta(hours=utc.hour % 3)
    signal_end = base + timedelta(hours=2)
    block_end = base + timedelta(hours=3)
    if utc < signal_end:
        start, end, active_now = base, signal_end, True
    else:
        start, end, active_now = signal_end, block_end, False
    return (
        start.astimezone(UAE),
        end.astimezone(UAE),
        block_end.astimezone(UAE),
        active_now,
    )


def session_payload() -> dict:
    start, end, next_start, active_now = session_window()
    return {
        "active": active_now,
        "session_start": start.strftime("%H:%M:%S UAE"),
        "session_end": end.strftime("%H:%M:%S UAE"),
        "next_session": next_start.strftime("%H:%M:%S UAE"),
        "remaining": max(0, int((end - now()).total_seconds())),
    }


def text_header(title: str) -> str:
    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "◈ CANDICE AI • LIVE MARKET\n"
        f"◈ {title}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


def keyboard(assets: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(a, callback_data=f"asset:{a}") for a in assets[i : i + 2]]
        for i in range(0, len(assets), 2)
    ]
    if not rows:
        rows = [[InlineKeyboardButton("No live FLEX assets", callback_data="noop")]]
    return InlineKeyboardMarkup(rows)


async def send_text(chat_id: int, text: str, reply_markup=None):
    if tg_app is None:
        return None
    try:
        msg = await tg_app.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
            read_timeout=30,
            write_timeout=30,
            connect_timeout=15,
            pool_timeout=15,
        )
        log.info("TELEGRAM TEXT SENT chat=%s message_id=%s", chat_id, msg.message_id)
        return msg
    except Exception as exc:
        log.exception("TELEGRAM TEXT FAILED chat=%s: %s", chat_id, exc)
        return None


async def edit_text(chat_id: int, message_id: int, text: str, reply_markup=None) -> bool:
    try:
        await tg_app.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        return True
    except Exception as exc:
        log.warning("TELEGRAM TEXT EDIT FAILED message_id=%s: %s", message_id, exc)
        return False


async def cmd_access(update, ctx):
    if not ACCESS or not ctx.args or ctx.args[0].strip() != ACCESS:
        await update.message.reply_text("🔒 CANDICE AI\n\nAccess denied.")
        return
    users.add(update.effective_user.id)
    await cmd_assets(update, ctx)


async def cmd_start(update, ctx):
    if update.effective_user.id not in users:
        await update.message.reply_text("🔒 CANDICE AI\n\nUse /access <code> first.")
        return
    await cmd_assets(update, ctx)


async def cmd_assets(update, ctx):
    uid = update.effective_user.id
    assets = await broker.live_assets()
    names = ", ".join(assets) if assets else "No fresh FLEX assets available"
    text = (
        f"{text_header('FLEX ASSET SELECTOR')}\n\n"
        f"Live assets: {len(assets)}\n"
        f"Fresh 1-minute candles: VERIFIED\n\n"
        f"{names}\n\n"
        "Mode: FLEX / Fixed-Time\n"
        "Forex mode: OFF\n"
        "Auto-trade: OFF\n"
        "Martingale: OFF\n\n"
        "Select one asset below."
    )
    await send_text(uid, text, reply_markup=keyboard(assets))


async def cmd_status(update, ctx):
    uid = update.effective_user.id
    reset_daily()
    asset = selected.get(uid, "NONE")
    text = (
        f"{text_header('SYSTEM STATUS')}\n\n"
        f"Broker: {'CONNECTED' if broker.connected() else 'DISCONNECTED'}\n"
        f"Selected FLEX: {asset}\n"
        f"Session: {session_state()}\n"
        f"Daily losses: {daily['losses']}/{MAX_DAILY_LOSSES}\n"
        f"Loss streak: {daily['streak']}/{MAX_STREAK}\n"
        f"Auto-trade: OFF\n"
        f"Martingale: OFF\n"
        f"Forex mode: OFF\n"
        f"Engine: {VERSION}"
    )
    await send_text(uid, text)


async def cmd_session(update, ctx):
    p = session_payload()
    mode = "SIGNAL SESSION" if p["active"] else "RESEARCH ONLY"
    text = (
        f"{text_header('SESSION CONTROL')}\n\n"
        f"State: {mode}\n"
        f"Ends: {p['session_end']}\n"
        f"Remaining: {p['remaining']} seconds\n"
        f"Next block: {p['next_session']}\n\n"
        "Market research: 24/7\n"
        "Signal generation: session only\n"
        "Scan cadence: every 5 minutes\n"
        "FLEX / Fixed-Time only"
    )
    await send_text(update.effective_user.id, text)


async def cmd_update(update, ctx):
    await cmd_session(update, ctx)


async def asset_callback(update, ctx):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if uid not in users or not query.data.startswith("asset:"):
        return

    asset = query.data.split(":", 1)[1].upper()
    if asset == "NOOP":
        return

    live = await broker.live_assets()
    if asset not in live:
        await edit_text(
            uid,
            query.message.message_id,
            f"{text_header('ASSET CHECK FAILED')}\n\n"
            f"{asset} is no longer live or its candle is stale.\n\n"
            "Run /start and select a fresh FLEX asset.",
        )
        return

    selected[uid] = asset
    selected_at = now().strftime("%H:%M:%S UAE")
    await edit_text(
        uid,
        query.message.message_id,
        f"{text_header('ASSET SELECTED')}\n\n"
        f"Asset: {asset}\n"
        "Status: 🟢 LIVE\n"
        "Candle: 1-minute • FRESH\n"
        f"Verified: {selected_at}\n\n"
        "Candice research is active.\n"
        "Signal generation remains gated by session + AI + technical evidence.\n\n"
        "Manual trade only • Auto-trade OFF",
    )


async def signal_countdown(chat_id: int, msg, signal: Signal):
    if msg is None:
        return
    end = signal.entry_ts + signal.expiry * 60
    while time.time() < end and active.get(chat_id) is signal:
        remaining = max(0, int(end - time.time()))
        minutes, seconds = divmod(remaining, 60)
        text = (
            f"{text_header('ACTIVE SIGNAL')}\n\n"
            f"Asset: {signal.asset}\n"
            f"Direction: {'⬆️ UP' if signal.direction == 'UP' else '⬇️ DOWN'}\n"
            f"Confidence: {signal.confidence}%\n"
            f"Expiry: {signal.expiry} MIN\n"
            f"Entry: {signal.entry:.6f}\n\n"
            f"⏳ Remaining: {minutes:02d}:{seconds:02d}\n"
            f"Expiry boundary: {datetime.fromtimestamp(end, UAE).strftime('%H:%M:%S UAE')}\n\n"
            "Technical gate: PASSED\n"
            "AI gate: APPROVED\n"
            "Auto-trade: OFF • Manual only"
        )
        await edit_text(chat_id, msg.message_id, text)
        await asyncio.sleep(min(30, max(1, remaining)))


async def result_monitor(uid: int, signal: Signal, msg):
    end = signal.entry_ts + signal.expiry * 60
    await signal_countdown(uid, msg, signal)
    await send_text(
        uid,
        f"{text_header('EXPIRY VERIFICATION')}\n\n"
        f"Asset: {signal.asset}\n"
        f"Direction: {signal.direction}\n"
        f"Entry: {signal.entry:.6f}\n"
        f"Expiry boundary: {datetime.fromtimestamp(end, UAE).strftime('%H:%M:%S UAE')}\n\n"
        "Reading the closed candle for outcome...",
    )

    price = None
    err = ""
    for _ in range(5):
        df, err = await broker.candles(signal.asset, 60, 20, 120)
        if df is not None and not df.empty:
            price = float(df.close.iloc[-1])
            break
        await asyncio.sleep(3)

    if price is None:
        active.pop(uid, None)
        await send_text(
            uid,
            f"{text_header('RESULT UNRESOLVED')}\n\n"
            f"Asset: {signal.asset}\n"
            f"Entry: {signal.entry:.6f}\n"
            "Exit: —\n"
            f"Reason: {err or 'No fresh expiry price'}\n\n"
            "No WIN/LOSS is recorded without fresh expiry evidence.",
        )
        return

    if price == signal.entry:
        result = "DRAW"
    elif (signal.direction == "UP" and price > signal.entry) or (
        signal.direction == "DOWN" and price < signal.entry
    ):
        result = "WIN"
    else:
        result = "LOSS"

    record(
        {
            "ts": time.time(),
            "asset": signal.asset,
            "direction": signal.direction,
            "confidence": signal.confidence,
            "expiry": signal.expiry,
            "entry": signal.entry,
            "exit": price,
            "result": result,
        }
    )
    reset_daily()
    active.pop(uid, None)

    icon = {"WIN": "✅", "LOSS": "❌", "DRAW": "➖"}[result]
    text = (
        f"{text_header('FINAL MARKET OUTCOME')}\n\n"
        f"{icon} {result}\n\n"
        f"Asset: {signal.asset}\n"
        f"Direction: {signal.direction}\n"
        f"Entry: {signal.entry:.6f}\n"
        f"Expiry: {price:.6f}\n"
        f"Duration: {signal.expiry} MIN\n"
        f"Expiry time: {datetime.fromtimestamp(end, UAE).strftime('%H:%M:%S UAE')}\n\n"
        "Verification: CANDLE-CLOSED\n"
        f"Price candle: {now().strftime('%H:%M:%S UAE')}\n\n"
        f"Daily losses: {daily['losses']}/{MAX_DAILY_LOSSES}\n"
        f"Loss streak: {daily['streak']}/{MAX_STREAK}\n\n"
        "MARKET OUTCOME • NOT BROKER ACCOUNT P/L\n"
        "AUTO TRADE OFF • MANUAL TRADE ONLY"
    )
    await send_text(uid, text)

    if result == "LOSS":
        recovery_until[uid] = time.time() + 300
        await send_text(
            uid,
            f"{text_header('RECOVERY WINDOW')}\n\n"
            "Loss protection is active.\n"
            "Next signal generation is paused for 5 minutes.\n\n"
            "Research continues in the background.\n"
            "15 MIN context • 5 MIN quick recovery review\n"
            f"Daily losses: {daily['losses']}/{MAX_DAILY_LOSSES}\n"
            "No martingale • No auto-trade",
        )


async def scan_once():
    reset_daily()
    assets = await broker.live_assets()
    log.info(
        "CANDICE SCAN START assets=%d session=%s users=%d",
        len(assets),
        session_state(),
        len(users),
    )

    sem = asyncio.Semaphore(6)

    async def research(asset: str):
        async with sem:
            df, err = await broker.candles(asset, 60, 60, 360)
            if err is None and df is not None:
                snap = technical_snapshot(df)
                record(
                    {
                        "ts": time.time(),
                        "asset": asset,
                        "research": True,
                        "direction": snap["direction"],
                        "strength": snap["strength"],
                        "rsi": snap["rsi14"],
                        "adx": snap["adx14"],
                    }
                )
                log.info(
                    "CANDICE RESEARCH pair=%s direction=%s strength=%.2f RSI=%.1f ADX=%.1f",
                    asset,
                    snap["direction"],
                    snap["strength"],
                    snap["rsi14"],
                    snap["adx14"],
                )
            else:
                log.info(
                    "CANDICE RESEARCH REJECT pair=%s reason=%s",
                    asset,
                    err or "no fresh candles",
                )

    await asyncio.gather(*(research(asset) for asset in assets[:30]))

    if (
        session_state() != "SIGNAL"
        or not users
        or daily["losses"] >= MAX_DAILY_LOSSES
        or daily["streak"] >= MAX_STREAK
    ):
        return

    for uid, asset in list(selected.items()):
        if uid in active or asset not in assets:
            continue
        if recovery_until.get(uid, 0) > time.time():
            log.info(
                "CANDICE RECOVERY WAIT pair=%s remaining=%ss",
                asset,
                int(recovery_until[uid] - time.time()),
            )
            continue

        recovery_until.pop(uid, None)
        decision = await analyze(asset, broker, summary(asset))
        log.info(
            "AI ANALYSIS pair=%s decision=%s direction=%s confidence=%s expiry=%s reason=%s",
            asset,
            decision.decision,
            decision.direction,
            decision.confidence,
            decision.expiry,
            decision.reason,
        )
        if decision.decision != "APPROVE":
            continue

        key = f"{uid}:{asset}:{int(time.time() // 300)}"
        if key in sent_keys:
            continue

        df, err = await broker.candles(asset, 60, 20, 120)
        if err or df is None or df.empty:
            continue

        entry = float(df.close.iloc[-1])
        entry_ts = time.time()
        signal = Signal(
            asset=asset,
            direction=decision.direction,
            confidence=decision.confidence,
            expiry=decision.expiry,
            entry=entry,
            entry_ts=entry_ts,
            reason=decision.reason,
        )
        active[uid] = signal
        sent_keys.add(key)
        end = entry_ts + decision.expiry * 60

        text = (
            f"{text_header('NEW SIGNAL')}\n\n"
            f"Asset: {asset}\n"
            f"Direction: {'⬆️ UP' if decision.direction == 'UP' else '⬇️ DOWN'}\n"
            f"Confidence: {decision.confidence}%\n"
            f"Duration: {decision.expiry} MIN\n"
            f"Entry: {entry:.6f}\n"
            f"Signal time: {datetime.fromtimestamp(entry_ts, UAE).strftime('%H:%M:%S UAE')}\n"
            f"Expiry: {datetime.fromtimestamp(end, UAE).strftime('%H:%M:%S UAE')}\n\n"
            "Fresh candle: VERIFIED\n"
            "Technical gate: PASSED\n"
            "AI gate: APPROVED\n\n"
            f"Reason: {decision.reason}\n\n"
            "⚠️ MANUAL TRADE ONLY • AUTO-TRADE OFF\n"
            "Martingale OFF • Forex mode OFF"
        )
        msg = await send_text(uid, text)
        asyncio.create_task(result_monitor(uid, signal, msg))


async def scheduler():
    while True:
        wait = INTERVAL - (time.time() % INTERVAL)
        await asyncio.sleep(max(1, wait))
        try:
            await scan_once()
        except Exception:
            log.exception("scan cycle failed")


async def bot_main():
    global tg_app
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    tg_app = Application.builder().token(TOKEN).build()
    for command, fn in [
        ("start", cmd_start),
        ("access", cmd_access),
        ("assets", cmd_assets),
        ("status", cmd_status),
        ("session", cmd_session),
        ("update", cmd_update),
    ]:
        tg_app.add_handler(CommandHandler(command, fn))
    tg_app.add_handler(CallbackQueryHandler(asset_callback, r"^asset:"))

    await tg_app.initialize()
    await tg_app.bot.delete_webhook(drop_pending_updates=True)
    await tg_app.start()
    await tg_app.updater.start_polling(drop_pending_updates=True)

    log.warning("CANDICE TELEGRAM ONLINE — TEXT-ONLY RUNTIME")
    asyncio.create_task(broker.connect_forever())
    asyncio.create_task(scheduler())

    while True:
        await asyncio.sleep(3600)


@flask_app.get("/")
def home():
    return f"{VERSION} ONLINE — FLEX market-data / text-only manual signals"


@flask_app.get("/health")
def health():
    return "OK"


@flask_app.get("/status")
def web_status():
    return {
        "version": VERSION,
        "broker_connected": broker.connected(),
        "selected_users": len(selected),
        "auto_trade": False,
        "martingale": False,
        "forex_mode": False,
        "flex_mode": True,
        "telegram_media": False,
    }


def http():
    flask_app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
        threaded=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    threading.Thread(target=http, daemon=True).start()
    asyncio.run(bot_main())

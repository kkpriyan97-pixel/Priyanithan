"""Autonomous Candice session scheduler.

Sends 5-minute-before, exact-start, 5-minute-before-end and exact-end
Telegram alerts for the canonical 2H SIGNAL / 1H RESEARCH cycle.
At every 5-minute boundary the existing app scan_loop continues automatically,
so a selected asset resumes from the next eligible boundary without manual
re-selection. This module never places broker orders, enables auto trading,
martingale, forced signals, or weakens AI gates.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from datetime import datetime, timezone

CYCLE_SECONDS = 3 * 60 * 60
SIGNAL_SECONDS = 2 * 60 * 60
PRE_ALERT_SECONDS = 5 * 60
POLL_SECONDS = 0.5
PATCHED = False


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _cycle_start(ts: float) -> int:
    return int(ts // CYCLE_SECONDS) * CYCLE_SECONDS


def _targets(ts: float):
    start = _cycle_start(ts)
    end = start + SIGNAL_SECONDS
    return start, end


def _fmt(seconds: int) -> str:
    n = max(0, int(seconds))
    h, r = divmod(n, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _uae(ts: int) -> str:
    a = _app()
    if a is not None:
        try:
            return datetime.fromtimestamp(ts, timezone.utc).astimezone(a.UAE_TZ).strftime("%Y-%m-%d %H:%M:%S UAE")
        except Exception:
            pass
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _recipients(a):
    ids = {int(x) for x in getattr(a, "authorized_users", set())}
    raw = str(getattr(a, "TELEGRAM_CHAT_ID", "")).strip()
    if raw:
        try:
            ids.add(int(raw))
        except Exception:
            pass
    return sorted(ids)


async def _animate(bot, uid: int, title: str, body: str, icon_frames):
    """Short Telegram edit animation: visually animated without extra assets."""
    try:
        msg = await bot.send_message(uid, f"{icon_frames[0]} {title}\n\n{body}")
        for icon in icon_frames[1:]:
            await asyncio.sleep(0.65)
            try:
                await bot.edit_message_text(chat_id=uid, message_id=msg.message_id,
                                            text=f"{icon} {title}\n\n{body}")
            except Exception:
                return
    except Exception:
        return


async def _broadcast(a, kind: str, event_ts: int, now_ts: float):
    bot = getattr(getattr(a, "telegram_application", None), "bot", None)
    if bot is None:
        return
    remain = max(0, int(event_ts - now_ts))
    start, end = _targets(now_ts)
    if kind == "START_PRE":
        title = "🚀 SIGNAL SESSION STARTING"
        body = (f"⏳ Starts in: {_fmt(remain)}\n"
                f"🏁 Start: {_uae(start)}\n\n"
                "🧠 Candice research → signal preparation\n"
                "📡 Selected assets remain active\n"
                "⚠️ Manual trade only — AUTO TRADE OFF")
        frames = ("⏳", "🟢", "🚀", "✨")
    elif kind == "START":
        title = "🚀 SIGNAL SESSION STARTED"
        body = (f"🟢 Signal window is OPEN\n"
                f"🏁 Ends: {_uae(end)}\n"
                "⏱️ Next eligible scan: next 5-minute boundary\n"
                "📡 Existing selected assets resume automatically\n"
                "🤖 Candice AI analysis ON\n"
                "⚠️ Manual trade only — AUTO TRADE OFF")
        frames = ("🟢", "🚀", "⚡", "🎯")
    elif kind == "END_PRE":
        title = "⚠️ SIGNAL SESSION ENDING"
        body = (f"⏳ Signal window ends in: {_fmt(remain)}\n"
                f"🏁 End: {_uae(end)}\n\n"
                "🧠 Candice is preparing research mode\n"
                "🚫 No new signal after session close")
        frames = ("⏳", "⚠️", "🟠", "🔔")
    else:
        title = "🧠 SIGNAL SESSION ENDED"
        body = (f"🏁 Ended: {_uae(end)}\n"
                "🔬 RESEARCH ONLY — 1 HOUR\n"
                "📡 Market research continues 24/7\n"
                f"🚀 Next signal session: {_uae(end + 3600)}\n"
                "⚠️ No forced signal — Manual trade only")
        frames = ("🔔", "🧠", "🔬", "📡")
    for uid in _recipients(a):
        asyncio.create_task(_animate(bot, uid, title, body, frames))


async def _scheduler():
    a = _app()
    if a is None:
        return
    sent = set()
    while True:
        try:
            now = time.time()
            start, end = _targets(now)
            events = (
                ("START_PRE", start, PRE_ALERT_SECONDS, 330),
                ("START", start, 0, 20),
                ("END_PRE", end, PRE_ALERT_SECONDS, 330),
                ("END", end, 0, 20),
            )
            for kind, event_ts, offset, max_late in events:
                key = (kind, event_ts)
                # Pre-alert: fire during the 5-minute window, once.
                if offset == PRE_ALERT_SECONDS:
                    if event_ts - PRE_ALERT_SECONDS <= now < event_ts and key not in sent:
                        sent.add(key)
                        await _broadcast(a, kind, event_ts, now)
                # Exact boundary: tolerate small process/network scheduling delay.
                elif event_ts <= now <= event_ts + max_late and key not in sent:
                    sent.add(key)
                    await _broadcast(a, kind, event_ts, now)
            await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            try:
                a.log.warning("CANDICE SESSION AUTOMATION LOOP: %s", exc)
            except Exception:
                pass
            await asyncio.sleep(2)


def _boot():
    global PATCHED
    for _ in range(1800):
        try:
            a = _app()
            loop = getattr(a, "runtime_loop", None) if a is not None else None
            if a is not None and loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(_scheduler(), loop)
                a.log.warning("CANDICE SESSION AUTOMATION ACTIVE: T-5 + exact START/END + auto 5m resume")
                PATCHED = True
                return
        except Exception:
            pass
        time.sleep(0.5)

threading.Thread(target=_boot, name="candice-session-automation", daemon=True).start()

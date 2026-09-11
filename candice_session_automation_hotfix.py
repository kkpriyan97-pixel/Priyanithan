"""Autonomous Candice session scheduler.

Sends 5-minute-before and exact-boundary Telegram alerts for the canonical
2H SIGNAL / 1H RESEARCH cycle. The existing app scan loop remains the source
of truth for 5-minute signal scanning; selected assets therefore resume at the
next eligible 5-minute boundary automatically after a session starts.

This module never places broker orders, enables auto trading, martingale,
forced signals, or weakened AI gates.
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


def _fmt(seconds: int) -> str:
    n = max(0, int(seconds))
    h, r = divmod(n, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _uae(ts: int) -> str:
    a = _app()
    try:
        tz = a.UAE_TZ if a is not None else timezone.utc
        return datetime.fromtimestamp(ts, timezone.utc).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S UAE")
    except Exception:
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
    """Short Telegram edit animation; no extra binary assets are required."""
    try:
        msg = await bot.send_message(uid, f"{icon_frames[0]} {title}\n\n{body}")
        for icon in icon_frames[1:]:
            await asyncio.sleep(0.65)
            try:
                await bot.edit_message_text(
                    chat_id=uid,
                    message_id=msg.message_id,
                    text=f"{icon} {title}\n\n{body}",
                )
            except Exception:
                return
    except Exception:
        return


async def _broadcast(a, kind: str, event_ts: int, now_ts: float):
    bot = getattr(getattr(a, "telegram_application", None), "bot", None)
    if bot is None:
        return
    remain = max(0, int(event_ts - now_ts))
    if kind in ("START_PRE", "START"):
        start = event_ts
        end = start + SIGNAL_SECONDS
    else:
        end = event_ts
        start = end - SIGNAL_SECONDS

    if kind == "START_PRE":
        title = "🚀 SIGNAL SESSION STARTING"
        body = (
            f"⏳ Starts in: {_fmt(remain)}\n"
            f"🏁 Start: {_uae(start)}\n\n"
            "🧠 Candice research → signal preparation\n"
            "📡 Selected assets remain active\n"
            "⚠️ Manual trade only — AUTO TRADE OFF"
        )
        frames = ("⏳", "🟢", "🚀", "✨")
    elif kind == "START":
        title = "🚀 SIGNAL SESSION STARTED"
        body = (
            "🟢 Signal window is OPEN\n"
            f"🏁 Ends: {_uae(end)}\n"
            "⏱️ Next eligible scan: next 5-minute boundary\n"
            "📡 Existing selected assets resume automatically\n"
            "🤖 Candice AI analysis ON\n"
            "⚠️ Manual trade only — AUTO TRADE OFF"
        )
        frames = ("🟢", "🚀", "⚡", "🎯")
    elif kind == "END_PRE":
        title = "⚠️ SIGNAL SESSION ENDING"
        body = (
            f"⏳ Signal window ends in: {_fmt(remain)}\n"
            f"🏁 End: {_uae(end)}\n\n"
            "🧠 Candice is preparing research mode\n"
            "🚫 No new signal after session close"
        )
        frames = ("⏳", "⚠️", "🟠", "🔔")
    else:
        title = "🧠 SIGNAL SESSION ENDED"
        body = (
            f"🏁 Ended: {_uae(end)}\n"
            "🔬 RESEARCH ONLY — 1 HOUR\n"
            "📡 Market research continues 24/7\n"
            f"🚀 Next signal session: {_uae(end + 3600)}\n"
            "⚠️ No forced signal — Manual trade only"
        )
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
            base = _cycle_start(now)
            # Watch the current cycle and the next cycle. This guarantees that
            # the T-5 start alert is not missed while the current period is
            # still active or during the research interval.
            starts = (base, base + CYCLE_SECONDS)
            ends = (base + SIGNAL_SECONDS, base + CYCLE_SECONDS + SIGNAL_SECONDS)

            for event_ts in starts:
                key = ("START_PRE", event_ts)
                if event_ts - PRE_ALERT_SECONDS <= now < event_ts and key not in sent:
                    sent.add(key)
                    await _broadcast(a, "START_PRE", event_ts, now)
                key = ("START", event_ts)
                if event_ts <= now <= event_ts + 20 and key not in sent:
                    sent.add(key)
                    await _broadcast(a, "START", event_ts, now)

            for event_ts in ends:
                key = ("END_PRE", event_ts)
                if event_ts - PRE_ALERT_SECONDS <= now < event_ts and key not in sent:
                    sent.add(key)
                    await _broadcast(a, "END_PRE", event_ts, now)
                key = ("END", event_ts)
                if event_ts <= now <= event_ts + 20 and key not in sent:
                    sent.add(key)
                    await _broadcast(a, "END", event_ts, now)

            # Keep memory bounded after long uptime.
            if len(sent) > 200:
                cutoff = base - (10 * CYCLE_SECONDS)
                sent = {x for x in sent if x[1] >= cutoff}
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
                a.log.warning(
                    "CANDICE SESSION AUTOMATION ACTIVE: T-5 + exact START/END + automatic 5m resume"
                )
                PATCHED = True
                return
        except Exception:
            pass
        time.sleep(0.5)

threading.Thread(target=_boot, name="candice-session-automation", daemon=True).start()

"""Telegram-visible Candice two-hour session status.

Adds a /session command and a lightweight status panel for authorized users.
It reports the current two-hour SIGNAL_SESSION or RESEARCH_ONLY state and a
countdown. It never enables trading, changes credentials, or places orders.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time

PATCHED = False


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _status_text(a):
    state = getattr(a, "candice_session_state", None)
    if callable(state):
        state = state()
    if not isinstance(state, dict):
        fn = getattr(a, "session_state", None)
        state = fn() if callable(fn) else {}
    active = bool(state.get("active"))
    remaining = max(0, int(state.get("remaining_seconds", 0)))
    h, rem = divmod(remaining, 3600)
    m, s = divmod(rem, 60)
    mode = "🟢 SIGNAL SESSION" if active else "🧠 RESEARCH ONLY"
    end = str(state.get("end_utc", "UNKNOWN"))
    return (
        "🤖 CANDICE AI SESSION\n\n"
        f"{mode}\n\n"
        f"⏳ Remaining: {h:02d}:{m:02d}:{s:02d}\n"
        f"🏁 Session end (UTC): {end}\n\n"
        "📡 Market research: 24/7\n"
        "📊 Signal generation: session only\n"
        "⚠️ MANUAL TRADE ONLY — AUTO TRADE OFF"
    )


async def _session_cmd(update, context):
    a = _app()
    user = update.effective_user
    if a is None or user is None:
        return
    authorized = getattr(a, "authorized_users", set())
    if int(user.id) not in authorized:
        await update.message.reply_text("Use /access YOUR_ACCESS_CODE first.")
        return
    await update.message.reply_text(_status_text(a))


def _patch(a):
    global PATCHED
    if getattr(a, "_CANDICE_SESSION_STATUS_V1", False):
        PATCHED = True
        return True
    app = getattr(a, "telegram_application", None)
    if app is None:
        return False
    try:
        from telegram.ext import CommandHandler
        app.add_handler(CommandHandler("session", _session_cmd))
    except Exception as exc:
        a.log.warning("CANDICE SESSION STATUS: handler unavailable: %s", exc)
        return False
    a.candice_session_status_text = lambda: _status_text(a)
    a._CANDICE_SESSION_STATUS_V1 = True
    a.log.warning("CANDICE SESSION STATUS V1 ACTIVE: /session shows 2-hour state")
    PATCHED = True
    return True


def _boot():
    for _ in range(1800):
        try:
            a = _app()
            if a is not None and _patch(a):
                return
        except Exception:
            pass
        time.sleep(0.2)

threading.Thread(target=_boot, name="candice-session-status", daemon=True).start()

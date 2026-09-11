"""Telegram-visible Candice two-hour session status.

Adds a reliable /session command for authorized users. It reports the current
2-hour signal / 2-hour research state, remaining time, UAE timing, and next
signal session. It never enables trading, changes credentials, or places
orders.
"""
from __future__ import annotations

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
    fn = getattr(a, "session_state", None)
    state = fn() if callable(fn) else getattr(a, "candice_session_state", {})
    if not isinstance(state, dict):
        state = {}
    active = bool(state.get("active"))
    remaining = max(0, int(state.get("remaining_seconds", 0)))
    h, rem = divmod(remaining, 3600)
    m, s = divmod(rem, 60)
    mode = "🟢 SIGNAL SESSION" if active else "🧠 RESEARCH ONLY"
    end_uae = str(state.get("end_uae", "UNKNOWN"))
    next_signal = str(state.get("next_signal_uae", "UNKNOWN"))
    return (
        "🤖 CANDICE AI SESSION\n\n"
        f"{mode}\n\n"
        f"⏳ Remaining: {h:02d}:{m:02d}:{s:02d}\n"
        f"🏁 Current period ends: {end_uae}\n"
        f"🚀 Next signal session: {next_signal}\n\n"
        "📡 Market research: 24/7\n"
        "📊 Signal generation: 2H session only\n"
        "⚠️ MANUAL TRADE ONLY — AUTO TRADE OFF"
    )


async def _session_cmd(update, context):
    a = _app()
    user = update.effective_user
    if a is None or user is None or update.message is None:
        return
    authorized = getattr(a, "authorized_users", set())
    if int(user.id) not in authorized:
        await update.message.reply_text("Use /access YOUR_ACCESS_CODE first.")
        return
    await update.message.reply_text(_status_text(a))


def _patch(a):
    global PATCHED
    if getattr(a, "_CANDICE_SESSION_STATUS_V2", False):
        PATCHED = True
        return True
    app = getattr(a, "telegram_application", None)
    if app is None:
        return False
    try:
        from telegram.ext import CommandHandler
        # build_application_hotfix already registers this handler. This fallback
        # is only for runtimes where the application was constructed elsewhere.
        if not getattr(a, "_CANDICE_SESSION_HANDLER_REGISTERED", False):
            app.add_handler(CommandHandler("session", _session_cmd))
    except Exception as exc:
        a.log.warning("CANDICE SESSION STATUS: handler unavailable: %s", exc)
        return False
    a.candice_session_status_text = lambda: _status_text(a)
    a._CANDICE_SESSION_STATUS_V2 = True
    a.log.warning("CANDICE SESSION STATUS V2 ACTIVE: /session + UAE countdown")
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

threading.Thread(target=_boot, name="candice-session-status-v2", daemon=True).start()

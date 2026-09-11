"""Final runtime guard for Candice session status and asset selection.

One deterministic session clock (2h signal / 1h research), one /session
handler, and one asset-selection handler. No broker order execution, auto
trading, martingale, forced signals, or weakened AI gates.
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PATCHED = False
UAE_TZ = ZoneInfo("Asia/Dubai")
SIGNAL_HOURS = 2
RESEARCH_HOURS = 1
CYCLE_HOURS = 3


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def session_state(ts=None):
    ts = time.time() if ts is None else float(ts)
    now = datetime.fromtimestamp(ts, timezone.utc)
    cycle_start = now.replace(hour=(now.hour // CYCLE_HOURS) * CYCLE_HOURS,
                              minute=0, second=0, microsecond=0)
    signal_end = cycle_start + timedelta(hours=SIGNAL_HOURS)
    cycle_end = cycle_start + timedelta(hours=CYCLE_HOURS)
    active = cycle_start <= now < signal_end
    boundary = signal_end if active else cycle_end
    next_signal = cycle_end
    return {
        "active": active,
        "mode": "SIGNAL_SESSION" if active else "RESEARCH_ONLY",
        "remaining_seconds": max(0.0, (boundary - now).total_seconds()),
        "start_ts": cycle_start.timestamp(),
        "end_ts": boundary.timestamp(),
        "end_uae": boundary.astimezone(UAE_TZ).strftime("%Y-%m-%d %H:%M:%S UAE"),
        "next_signal_ts": next_signal.timestamp(),
        "next_signal_uae": next_signal.astimezone(UAE_TZ).strftime("%Y-%m-%d %H:%M:%S UAE"),
    }


def _session_text(a):
    s = session_state()
    remaining = int(s["remaining_seconds"])
    h, r = divmod(remaining, 3600)
    m, sec = divmod(r, 60)
    mode = "🟢 SIGNAL SESSION" if s["active"] else "🧠 RESEARCH ONLY"
    return (
        "🤖 CANDICE AI SESSION\n\n"
        f"{mode}\n\n"
        f"⏳ Remaining: {h:02d}:{m:02d}:{sec:02d}\n"
        f"🏁 Current period ends: {s['end_uae']}\n"
        f"🚀 Next signal session: {s['next_signal_uae']}\n\n"
        "📡 Market research: 24/7\n"
        "📊 Signal generation: 2H signal / 1H research cycle\n"
        "⚠️ MANUAL TRADE ONLY — AUTO TRADE OFF"
    )


async def _session_cmd(update, context):
    a = _app()
    user = update.effective_user
    if a is None or user is None or update.message is None:
        return
    if int(user.id) not in getattr(a, "authorized_users", set()):
        await update.message.reply_text("Use /access YOUR_ACCESS_CODE first.")
        return
    await update.message.reply_text(_session_text(a))


_ORIGINAL_ASSET_CALLBACK = None


async def _asset_callback(update, context):
    a = _app()
    q = update.callback_query
    if a is None or q is None:
        return
    data = q.data or ""
    if not data.startswith("asset:"):
        if _ORIGINAL_ASSET_CALLBACK is not None:
            return await _ORIGINAL_ASSET_CALLBACK(update, context)
        return
    await q.answer()
    uid = int(q.from_user.id)
    if uid not in getattr(a, "authorized_users", set()):
        await q.message.reply_text("❌ Access required.")
        return
    pair = data.split(":", 1)[1].strip().upper()
    ok, err = await a.verify_live_pair(pair)
    if not ok:
        await q.message.reply_text(
            f"⚠️ {pair} is not currently live: {err}\n\nChoose another live asset."
        )
        return
    a.selected_asset[uid] = pair
    a.active_signal.pop(uid, None)
    s = session_state()
    if s["active"]:
        text = (
            f"✅ ASSET SELECTED — {pair}\n\n"
            "🟢 Fresh 1-minute candle verified\n"
            "🤖 Candice AI analysis ON\n"
            "🚀 SIGNAL SESSION ACTIVE\n"
            f"🏁 Signal session ends: {s['end_uae']}\n"
            f"🚀 Next signal session: {s['next_signal_uae']}\n"
            "⏱️ AI duration = 2 / 3 / 5 / 10 / 15 MIN\n"
            "⚠️ MANUAL TRADE ONLY — AUTO TRADE OFF"
        )
    else:
        remaining = int(s["remaining_seconds"])
        h, r = divmod(remaining, 3600)
        m, sec = divmod(r, 60)
        text = (
            f"🧠 {pair} — RESEARCH INTERVAL\n\n"
            "🟢 Fresh 1-minute candle verified\n"
            "🤖 Candice AI research ON\n"
            "📡 Live market research continues 24/7\n"
            "🚫 Signal generation is paused during this interval.\n\n"
            f"⏳ Research remaining: {h:02d}:{m:02d}:{sec:02d}\n"
            f"🏁 Research ends: {s['end_uae']}\n"
            f"🚀 Next signal session: {s['next_signal_uae']}\n\n"
            "⚠️ No forced signal • Manual trade only"
        )
    await q.message.reply_text(text)


def _patch():
    global PATCHED, _ORIGINAL_ASSET_CALLBACK
    a = _app()
    if a is None:
        return False

    # Install the canonical clock immediately. The session-gate hotfix may
    # still wrap analyze/scan, but UI and /session always use this clock.
    a.session_state = session_state
    a.signal_session_active = lambda ts=None: bool(session_state(ts)["active"])
    a.candice_session_state = session_state()
    a._CANDICE_FINAL_SESSION_CLOCK = True

    callback = getattr(a, "assets_callback", None)
    if callable(callback) and not getattr(a, "_CANDICE_FINAL_ASSET_CALLBACK", False):
        _ORIGINAL_ASSET_CALLBACK = callback
        a.assets_callback = _asset_callback
        a._CANDICE_FINAL_ASSET_CALLBACK = True

    # app.py resolves build_application from builtins. Replace it with a
    # self-contained builder that directly registers the final handlers, so
    # background patch timing cannot leave /session or asset selection stale.
    import builtins
    builder = getattr(builtins, "build_application", None)
    if callable(builder) and not getattr(builder, "_CANDICE_FINAL_BUILDER", False):
        def final_build_application():
            app = _app()
            application = app.Application.builder().token(app.TELEGRAM_BOT_TOKEN).updater(None).build()
            application.add_handler(app.CommandHandler("start", app.start_cmd))
            application.add_handler(app.CommandHandler("access", app.access_cmd))
            application.add_handler(app.CommandHandler("assets", app.assets_cmd))
            application.add_handler(app.CommandHandler("session", _session_cmd))
            application.add_handler(app.CallbackQueryHandler(_asset_callback, pattern=r"^(asset:|assets:)"))
            app._CANDICE_SESSION_HANDLER_REGISTERED = True
            app.log.warning("CANDICE FINAL RUNTIME: /session + canonical asset UI registered")
            return application
        final_build_application._CANDICE_FINAL_BUILDER = True
        builtins.build_application = final_build_application

    a.log.warning("CANDICE FINAL SESSION CLOCK ACTIVE: 2H SIGNAL / 1H RESEARCH")
    PATCHED = True
    return True


def _boot():
    for _ in range(1800):
        try:
            if _patch():
                return
        except Exception:
            pass
        time.sleep(0.2)

threading.Thread(target=_boot, name="candice-final-runtime", daemon=True).start()

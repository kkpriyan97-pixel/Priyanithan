"""Reliable Telegram application builder for the single live app module.

Render starts app.py as __main__. This shim resolves that exact module so the
webhook, OlympTrade connection, and Telegram handlers share one runtime state.
The /session command is registered directly here rather than depending on a
background hotfix import, eliminating startup races. No broker order execution
is enabled.
"""
import builtins
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UAE_TZ = ZoneInfo("Asia/Dubai")


def _app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    import app
    return app


def _session_text(app):
    state_fn = getattr(app, "session_state", None)
    state = state_fn() if callable(state_fn) else None
    if not isinstance(state, dict):
        # Safe fallback: same repeating 2h signal / 2h research schedule.
        now = datetime.now(timezone.utc)
        cycle_start = now.replace(hour=(now.hour // 4) * 4, minute=0, second=0, microsecond=0)
        signal_end = cycle_start + timedelta(hours=2)
        cycle_end = cycle_start + timedelta(hours=4)
        active = now < signal_end
        boundary = signal_end if active else cycle_end
        next_signal = cycle_start if active else cycle_end
        state = {
            "active": active,
            "remaining_seconds": max(0, (boundary - now).total_seconds()),
            "end_uae": boundary.astimezone(UAE_TZ).strftime("%Y-%m-%d %H:%M:%S UAE"),
            "next_signal_uae": next_signal.astimezone(UAE_TZ).strftime("%Y-%m-%d %H:%M:%S UAE"),
        }
    remaining = max(0, int(state.get("remaining_seconds", 0)))
    hours, rem = divmod(remaining, 3600)
    minutes, seconds = divmod(rem, 60)
    mode = "🟢 SIGNAL SESSION" if state.get("active") else "🧠 RESEARCH ONLY"
    return (
        "🤖 CANDICE AI SESSION\n\n"
        f"{mode}\n\n"
        f"⏳ Remaining: {hours:02d}:{minutes:02d}:{seconds:02d}\n"
        f"🏁 Current period ends: {state.get('end_uae', 'UNKNOWN')}\n"
        f"🚀 Next signal session: {state.get('next_signal_uae', 'UNKNOWN')}\n\n"
        "📡 Market research: 24/7\n"
        "📊 Signal generation: 2H session only\n"
        "⚠️ MANUAL TRADE ONLY — AUTO TRADE OFF"
    )


async def _session_cmd(update, context):
    app = _app()
    user = update.effective_user
    if app is None or user is None or update.message is None:
        return
    authorized = getattr(app, "authorized_users", set())
    if int(user.id) not in authorized:
        await update.message.reply_text("Use /access YOUR_ACCESS_CODE first.")
        return
    await update.message.reply_text(_session_text(app))


def build_application():
    app = _app()
    application = app.Application.builder().token(app.TELEGRAM_BOT_TOKEN).updater(None).build()
    application.add_handler(app.CommandHandler("start", app.start_cmd))
    application.add_handler(app.CommandHandler("access", app.access_cmd))
    application.add_handler(app.CommandHandler("assets", app.assets_cmd))
    application.add_handler(app.CommandHandler("session", _session_cmd))
    application.add_handler(app.CallbackQueryHandler(app.assets_callback, pattern=r"^(asset:|assets:)"))
    app._CANDICE_SESSION_HANDLER_REGISTERED = True
    app.log.warning("CANDICE /session COMMAND REGISTERED IN APPLICATION BUILD")
    return application


builtins.build_application = build_application

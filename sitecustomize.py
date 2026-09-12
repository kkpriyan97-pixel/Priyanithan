"""Canonical startup bootstrap for Priyanithan."""
import builtins
import importlib
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UAE_TZ = ZoneInfo("Asia/Dubai")


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _session_text(a):
    now = datetime.now(timezone.utc)
    cycle_start = now.replace(hour=(now.hour // 3) * 3, minute=0, second=0, microsecond=0)
    signal_end = cycle_start + timedelta(hours=2)
    cycle_end = cycle_start + timedelta(hours=3)
    active = now < signal_end
    boundary = signal_end if active else cycle_end
    next_signal = cycle_start + timedelta(hours=3) if active else cycle_end
    remaining = max(0, int((boundary - now).total_seconds()))
    h, r = divmod(remaining, 3600)
    m, s = divmod(r, 60)
    mode = "🟢 SIGNAL SESSION" if active else "🧠 RESEARCH ONLY"
    return ("🤖 CANDICE AI SESSION\n\n" f"{mode}\n\n" f"⏳ Remaining: {h:02d}:{m:02d}:{s:02d}\n"
            f"🏁 Current period ends: {boundary.astimezone(UAE_TZ):%Y-%m-%d %H:%M:%S} UAE\n"
            f"🚀 Next signal session: {next_signal.astimezone(UAE_TZ):%Y-%m-%d %H:%M:%S} UAE\n\n"
            "📡 Market research: 24/7\n"
            "📊 Signal generation: 2H signal / 1H research cycle\n"
            "⚠️ MANUAL TRADE ONLY — AUTO TRADE OFF")


async def _session_cmd(update, context):
    a = _app(); user = update.effective_user
    if a is None or user is None or update.message is None:
        return
    if int(user.id) not in getattr(a, "authorized_users", set()):
        await update.message.reply_text("Use /access YOUR_ACCESS_CODE first.")
        return
    await update.message.reply_text(_session_text(a))


def build_application():
    a = _app()
    application = a.Application.builder().token(a.TELEGRAM_BOT_TOKEN).updater(None).build()
    application.add_handler(a.CommandHandler("start", a.start_cmd))
    application.add_handler(a.CommandHandler("access", a.access_cmd))
    application.add_handler(a.CommandHandler("assets", a.assets_cmd))
    application.add_handler(a.CommandHandler("session", _session_cmd))

    visual = importlib.import_module("candice_visual_asset_ui")
    # Replace the app callback reference as well as the handler. This prevents
    # any later code from registering the old text-only callback.
    native = getattr(a, "assets_callback", None)
    if native is not None and getattr(native, "__name__", "") != "_callback":
        a._native_assets_callback = native
    a.assets_callback = visual._callback
    visual.force_register(application, a)
    a.log.warning("CANDICE CANONICAL APPLICATION BUILDER ACTIVE: forced animated asset handler")
    return application


builtins.build_application = build_application
for _module in ("candice_visual_cards_hotfix", "candice_visual_asset_ui"):
    try:
        importlib.import_module(_module)
    except Exception:
        pass

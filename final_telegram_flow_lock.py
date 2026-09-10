"""Hard lock Telegram onto the final live-asset selector.
Removes every old /start and /access CommandHandler and installs exactly one
final handler for each. No DEMO/REAL chooser and no automatic trading.
"""
import asyncio
import os
import sys
import threading
import time
from datetime import timedelta


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _install(a):
    application = getattr(a, "telegram_application", None)
    if application is None:
        return False
    import final_asset_selection_flow as flow
    from telegram.ext import CommandHandler

    # Remove ALL legacy /start and /access handlers, regardless of which
    # previous hotfix installed them. This is the key fix for the old screen.
    removed = 0
    for group, handlers in list(getattr(application, "handlers", {}).items()):
        for handler in list(handlers):
            commands = set(getattr(handler, "commands", set()) or set())
            if commands.intersection({"start", "access"}):
                application.remove_handler(handler, group=group)
                removed += 1

    async def final_start(update, context):
        await flow._start_cmd(update, context)

    async def final_access(update, context):
        try:
            a.remember_chat(update)
        except Exception:
            pass
        expected = os.getenv("ACCESS_CODE", "").strip()
        code = (context.args or [""])[0].strip()
        if not expected:
            await update.message.reply_text("ACCESS_CODE is not configured.")
            return
        if code != expected:
            await update.message.reply_text("❌ Invalid access code.")
            return
        uid = int(update.effective_user.id)
        a.authorized_users.add(uid)
        try:
            import fast_manual_entry
            fast_manual_entry.set_mode(uid, "DEMO")
        except Exception:
            pass
        await update.message.reply_text("✅ ACCESS VERIFIED\n\n📊 LIVE ASSET SELECTION")
        await flow._send_asset_menu(context.bot, uid)

    final_start._FINAL_DIRECT_START = True
    final_access._FINAL_DIRECT_ACCESS = True
    application.add_handler(CommandHandler("start", final_start), group=0)
    application.add_handler(CommandHandler("access", final_access), group=0)

    # Lock the scanner to the selected-asset implementation and exact 5-min boundary.
    a.scan_cycle = flow._scan_cycle
    def exact_wait():
        async def _wait():
            now = a.now_uae()
            nxt = ((now.minute // 5) + 1) * 5
            target = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0) if nxt >= 60 else now.replace(minute=nxt, second=0, microsecond=0)
            await asyncio.sleep(max(1, (target - now).total_seconds()))
        return _wait()
    a.wait_until_next_5min_uae = exact_wait
    a._FINAL_TELEGRAM_DIRECT_LOCK = True
    a.log.warning("FINAL DIRECT TELEGRAM LOCK: removed=%s old /start,/access handlers; LIVE ASSET buttons only; exact 5-minute selected scan", removed)
    return True


def _boot():
    for _ in range(1800):
        try:
            a = _app()
            if a and getattr(a, "telegram_application", None) is not None:
                if _install(a):
                    return
        except Exception:
            a = _app()
            if a is not None and hasattr(a, "log"):
                a.log.exception("FINAL DIRECT TELEGRAM LOCK RETRY FAILED")
        time.sleep(0.25)


threading.Thread(target=_boot, name="final-direct-telegram-lock", daemon=True).start()

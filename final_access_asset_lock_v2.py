"""Final Telegram access/asset selector lock.

Removes legacy /start and /access handlers and installs exactly one of each.
Access always enters the live-asset selector; there is no DEMO/REAL chooser.
Manual DEMO testing only. This module never places broker orders.
"""
import asyncio
import os
import sys
import threading
import time


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _handler_commands(handler):
    try:
        return {str(x).lower().lstrip("/") for x in (getattr(handler, "commands", set()) or set())}
    except Exception:
        return set()


def _remove_legacy(application):
    removed = 0
    for group, handlers in list(getattr(application, "handlers", {}).items()):
        for handler in list(handlers):
            commands = _handler_commands(handler)
            if commands.intersection({"start", "access"}):
                try:
                    application.remove_handler(handler, group=group)
                    removed += 1
                except Exception:
                    pass
    return removed


def _install(a):
    application = getattr(a, "telegram_application", None)
    if application is None:
        return False

    import final_asset_selection_flow as flow
    from telegram.ext import CommandHandler

    # Remove every legacy command handler first, including handlers installed by
    # old startup patches. PTB handlers are matched by group, so leaving an old
    # earlier-group handler can otherwise bypass the final flow.
    _remove_legacy(application)

    async def final_access(update, context):
        try:
            a.remember_chat(update)
        except Exception:
            pass
        expected = os.getenv("ACCESS_CODE", "").strip()
        code = (context.args or [""])[0].strip()
        if not expected:
            await update.message.reply_text("❌ ACCESS_CODE is not configured.")
            return
        if code != expected:
            await update.message.reply_text("❌ Invalid access code.")
            return

        uid = int(update.effective_user.id)
        a.authorized_users.add(uid)
        # DEMO is fixed internally for testing; never expose a REAL mode chooser.
        try:
            import fast_manual_entry
            fast_manual_entry.set_mode(uid, "DEMO")
        except Exception:
            pass
        await update.message.reply_text("✅ ACCESS VERIFIED\n\n📊 LIVE ASSET SELECTION")
        await flow._send_asset_menu(context.bot, uid)

    start_handler = CommandHandler("start", flow._start_cmd)
    access_handler = CommandHandler("access", final_access)
    start_handler._PRIYANITHAN_FINAL_ACCESS_V2 = True
    access_handler._PRIYANITHAN_FINAL_ACCESS_V2 = True
    application.add_handler(start_handler, group=0)
    application.add_handler(access_handler, group=0)

    # The selected-asset scanner is the only scan implementation used after
    # selection. Keep the legacy broad scanner out of this flow.
    a.scan_cycle = flow._scan_cycle

    # Signal cycles run once per minute; AI still chooses the supported expiry.
    async def every_minute():
        await asyncio.sleep(60)
    a.wait_until_next_5min_uae = every_minute
    a._FINAL_ACCESS_SINGLE_HANDLER_V2 = True
    a.log.warning("FINAL ACCESS V2: legacy /start,/access removed; access -> live assets; manual DEMO only")
    return True


def _boot():
    # Startup ordering varies between Render/Telegram initialization and the
    # sitecustomize hook. Re-apply briefly so late legacy patches cannot win.
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            a = _app()
            if a and getattr(a, "telegram_application", None) is not None:
                _install(a)
        except Exception:
            pass
        time.sleep(0.5)


threading.Thread(target=_boot, name="final-access-asset-lock-v2", daemon=True).start()

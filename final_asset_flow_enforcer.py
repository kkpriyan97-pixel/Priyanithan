"""Final hard lock for the selected-asset Telegram flow.

This is the last startup patch. It removes the old DEMO/REAL -> scan screen,
opens the live asset buttons after access, disables the old /trade/mode action,
and restores an exact 5-minute scanner boundary. It never places trades.
"""
import asyncio
import sys
import threading
import time
from datetime import timedelta


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _exact_wait_factory(a):
    async def exact_wait():
        now = a.now_uae()
        next_minute = ((now.minute // 5) + 1) * 5
        if next_minute >= 60:
            target = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        else:
            target = now.replace(minute=next_minute, second=0, microsecond=0)
        await asyncio.sleep(max(1, (target - now).total_seconds()))
    return exact_wait


def _install(a):
    import final_asset_selection_flow as flow
    application = getattr(a, "telegram_application", None)
    if application is None:
        return False

    # The selected-asset flow owns scanning. The old one-minute cadence patch
    # is explicitly overridden here.
    a.wait_until_next_5min_uae = _exact_wait_factory(a)
    a._FINAL_EXACT_5MIN_CADENCE = True

    # Install the selected asset callback/scan implementation.
    flow.install()

    # /start must ALWAYS open the live asset buttons, never the old mode/scan UI.
    for group in getattr(application, "handlers", {}).values():
        for handler in group:
            commands = getattr(handler, "commands", set())
            if "start" in commands:
                handler.callback = flow._start_cmd
                handler.callback._FINAL_HARD_START = True
            elif "access" in commands:
                async def hard_access(update, context):
                    try:
                        a.remember_chat(update)
                    except Exception:
                        pass
                    expected = __import__("os").getenv("ACCESS_CODE")
                    code = (context.args or [""])[0].strip()
                    if not expected:
                        await update.message.reply_text("ACCESS_CODE is not configured.")
                        return
                    if code != expected:
                        await update.message.reply_text("❌ Invalid access code.")
                        return
                    uid = int(update.effective_user.id)
                    a.authorized_users.add(uid)
                    # Demo is the fixed testing mode for this final flow.
                    try:
                        import fast_manual_entry
                        fast_manual_entry.set_mode(uid, "DEMO")
                    except Exception:
                        pass
                    await update.message.reply_text("✅ ACCESS VERIFIED\n\n📊 Choose a LIVE OlympTrade asset:")
                    await flow._send_asset_menu(context.bot, uid)
                handler.callback = hard_access
                handler.callback._FINAL_HARD_ACCESS = True

    # Replace the old browser-facing /trade/mode endpoint so a stale DEMO/REAL
    # button can never start the broad scanner. It only opens the asset menu.
    flask = getattr(a, "app", None)
    if flask is not None:
        endpoint = "final_trade_mode"
        if endpoint in flask.view_functions:
            def disabled_mode_route():
                from flask import request
                import fast_manual_entry
                raw = fast_manual_entry.unpack(request.args.get("token", ""))
                if not raw:
                    return "Invalid mode link", 403
                parts = raw.split("|")
                if len(parts) != 3:
                    return "Invalid mode link", 400
                try:
                    cid = int(parts[0])
                except Exception:
                    return "Invalid mode link", 400
                if cid not in {int(x) for x in a.authorized_users}:
                    return "Access not authorized.", 403
                fast_manual_entry.set_mode(cid, "DEMO")
                loop = getattr(a, "runtime_loop", None)
                app_obj = getattr(a, "telegram_application", None)
                if loop and app_obj:
                    async def show_menu():
                        await flow._send_asset_menu(app_obj.bot, cid, title="📊 SELECT A LIVE OLYMPTRADE ASSET")
                    try:
                        asyncio.run_coroutine_threadsafe(show_menu(), loop)
                    except Exception:
                        pass
                return "<meta name='viewport' content='width=device-width,initial-scale=1'><body style='font-family:system-ui;padding:28px'><h2>📊 LIVE ASSET SELECTION</h2><p>Choose the asset from Telegram.</p></body>"
            flask.view_functions[endpoint] = disabled_mode_route

    a._FINAL_ASSET_FLOW_ENFORCED = True
    a.log.warning("FINAL ASSET FLOW ENFORCED: access -> live asset buttons; no mode screen; exact 5-minute cycle; Trade Now disabled")
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
                a.log.exception("FINAL ASSET FLOW ENFORCER RETRY FAILED")
        time.sleep(0.5)


threading.Thread(target=_boot, name="final-asset-flow-enforcer", daemon=True).start()

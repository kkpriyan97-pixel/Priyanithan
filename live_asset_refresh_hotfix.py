"""Ensure Telegram live-asset refresh uses the same running app state.

Render starts app.py as __main__. Resolve that module so Telegram discovery,
broker connection state, and asset cache are shared. No broker order execution.
"""
import sys
import threading
import time

PATCHED = False


def _app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    import app
    return app


def _patch():
    global PATCHED
    try:
        app = _app()
    except Exception:
        return False

    if getattr(app, "_LIVE_ASSET_REFRESH_V1", False):
        PATCHED = True
        return True

    original = getattr(app, "send_asset_menu", None)
    if not callable(original):
        return False

    async def patched_send_asset_menu(bot, user_id, note=None):
        live = await app.discover_live_assets()
        if not live:
            text = (
                "⚠️ NO LIVE ASSETS AVAILABLE\n\n"
                "OlympTrade returned no fresh 1-minute candle.\n"
                "Closed/stale assets are never shown.\n\n"
                "Tap the button below to scan again."
            )
            if note:
                text = note + "\n\n" + text
            await bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=app.asset_keyboard([], 0),
            )
            return False

        text = (
            "📊 LIVE ASSET SELECTION\n\n"
            "Choose one asset below.\n"
            "🟢 = fresh OlympTrade candle verified\n"
            "⏱️ Next signal window = next 5 minutes\n"
            "⏱️ AI duration = 2 / 3 / 5 / 10 / 15 MIN\n"
            "⚠️ Manual trade only — AUTO TRADE OFF"
        )
        if note:
            text = note + "\n\n" + text
        await bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=app.asset_keyboard(live, 0),
        )
        return True

    app.send_asset_menu = patched_send_asset_menu
    app._LIVE_ASSET_REFRESH_V1 = True
    try:
        app.log.warning("LIVE ASSET REFRESH V1 ACTIVE: no-live menu now includes refresh button")
    except Exception:
        pass
    PATCHED = True
    return True


def _boot():
    for _ in range(1800):
        try:
            if _patch():
                return
        except Exception:
            pass
        time.sleep(0.1)


threading.Thread(target=_boot, name="live-asset-refresh-boot", daemon=True).start()

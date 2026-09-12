"""FLEX-only safety guard for Priyanithan.

This guard is mode/UI state only. It never places broker orders and never
filters assets by symbol suffix: OTC assets can be valid FLEX assets.
"""
import functools
import sys
import threading
import time

FLEX_ONLY = True
AUTO_TRADE = False
MARTINGALE = False
ALLOWED_DURATIONS = (2, 3, 5, 10, 15)


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _install(a):
    if getattr(a, "_CANDICE_FLEX_ONLY", False):
        return True
    a.AUTO_TRADE = False
    a.MARTINGALE = False
    a.ALLOWED_DURATIONS = ALLOWED_DURATIONS
    a.TRADING_MODE = "FLEX"
    a.FLEX_ONLY = True

    original_menu = a.send_asset_menu

    @functools.wraps(original_menu)
    async def flex_asset_menu(bot, user_id, note=None):
        flex_note = "🟢 FLEX / FIXED-TIME MODE — FOREX MODE OFF"
        merged = f"{flex_note}\n\n{note}" if note else flex_note
        return await original_menu(bot, user_id, merged)

    a.send_asset_menu = flex_asset_menu

    original_status = a.status

    @functools.wraps(original_status)
    def flex_status():
        payload = original_status()
        if isinstance(payload, dict):
            payload["trading_mode"] = "FLEX"
            payload["forex_mode"] = False
            payload["auto_trade"] = False
            payload["martingale"] = False
        return payload

    a.status = flex_status
    a._CANDICE_FLEX_ONLY = True
    a.log.warning("CANDICE FLEX-ONLY GUARD ACTIVE: FLEX/FIXED-TIME only; Forex mode OFF; auto-trade OFF; martingale OFF")
    return True


def _boot():
    # sitecustomize runs before app.py is fully initialized, so wait for the
    # canonical app module instead of silently failing at import time.
    for _ in range(300):
        try:
            a = _app()
            if a is not None and callable(getattr(a, "send_asset_menu", None)) and callable(getattr(a, "status", None)):
                _install(a)
                return
        except Exception:
            pass
        time.sleep(0.2)


threading.Thread(target=_boot, name="candice-flex-only", daemon=True).start()

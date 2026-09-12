"""FLEX-only safety and asset-selection guard for Priyanithan.

This layer does not place broker orders. It keeps the application in manual
Fixed Time/FLEX mode and prevents the asset selector from accidentally being
presented as a Forex trading workflow.
"""
import functools
import os


FLEX_ONLY = True
AUTO_TRADE = False
MARTINGALE = False
ALLOWED_DURATIONS = (2, 3, 5, 10, 15)


def _install(a):
    a.AUTO_TRADE = False
    a.MARTINGALE = False
    a.ALLOWED_DURATIONS = ALLOWED_DURATIONS
    a.TRADING_MODE = "FLEX"
    a.FLEX_ONLY = True

    # Never let a Forex-mode label/configuration leak into the user-facing
    # selector. FLEX/Fixed-Time assets may include OTC symbols on weekends.
    original_menu = a.send_asset_menu

    @functools.wraps(original_menu)
    async def flex_asset_menu(bot, user_id, note=None):
        flex_note = "🟢 FLEX / FIXED-TIME MODE — FOREX MODE OFF"
        merged = f"{flex_note}\n\n{note}" if note else flex_note
        return await original_menu(bot, user_id, merged)

    a.send_asset_menu = flex_asset_menu

    # Mark the mode explicitly for diagnostics/health checks without exposing
    # secrets or changing broker authentication.
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
    a.log.warning("CANDICE FLEX-ONLY GUARD ACTIVE: FLEX/FIXED-TIME only; Forex mode OFF; auto-trade OFF; martingale OFF")


try:
    import sys
    _a = sys.modules.get("__main__")
    if _a is not None and getattr(_a, "__file__", "").endswith("app.py"):
        _install(_a)
except Exception as exc:
    try:
        import logging
        logging.getLogger("priyanithan").exception("FLEX guard install failed: %s", exc)
    except Exception:
        pass

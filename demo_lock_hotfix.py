"""DEMO-only safety lock for the manual Telegram trade flow.

This module never places broker orders and never logs into Olymptrade.
It prevents REAL mode links from selecting a real mode and makes the
Trade Now page point to Olymptrade's official demo-account page.
"""
import sys
import threading
import time

DEMO_LANDING = "https://olymptrade.com/pages/trading/account/free-demo/"


def _app():
    m = sys.modules.get("app")
    if m is not None:
        return m
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return None


def _install():
    try:
        import fast_manual_entry as f
    except Exception:
        return False

    a = _app()
    if a is None:
        return False

    # DEMO is the only supported bot mode.
    f.DEMO_URL = DEMO_LANDING
    f.REAL_URL = DEMO_LANDING

    def demo_set_mode(cid, value):
        if str(value).upper() != "DEMO":
            return False
        with f.LOCK:
            f.MODES[int(cid)] = "DEMO"
        return True

    def demo_mode_markup(cid):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🧪 DEMO ONLY", url=f.mode_url(cid, "DEMO"))
        ]])

    def demo_signal_markup(cid, text):
        data = f.parse_signal(text)
        if not data or f.mode(cid) != "DEMO":
            return None
        pair, direction, entry, expiry = data
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "⚡ TRADE NOW — DEMO",
                web_app=WebAppInfo(url=f.trade_app_url(cid, pair, direction, entry, expiry)),
            )
        ]])

    f.set_mode = demo_set_mode
    f.mode_markup = demo_mode_markup
    f.signal_markup = demo_signal_markup

    # Existing Flask routes are already registered during startup. Replace
    # their view functions instead of adding routes after first request.
    flask = getattr(a, "app", None)
    if flask is not None:
        original_mode = flask.view_functions.get("final_trade_mode")
        if original_mode is not None and not getattr(original_mode, "_DEMO_LOCKED", False):
            def safe_mode():
                from flask import request
                raw = f.unpack(request.args.get("token", ""))
                if not raw:
                    return "Invalid mode link", 403
                parts = raw.split("|")
                if len(parts) != 3 or parts[1] != "DEMO":
                    return "REAL mode is disabled. Use DEMO only.", 403
                return original_mode()
            safe_mode._DEMO_LOCKED = True
            flask.view_functions["final_trade_mode"] = safe_mode

        original_trade = flask.view_functions.get("final_trade_app")
        if original_trade is not None and not getattr(original_trade, "_DEMO_LOCKED", False):
            def safe_trade():
                from flask import request
                raw = f.unpack(request.args.get("token", ""))
                if not raw:
                    return "Invalid trade link", 403
                parts = raw.split("|")
                if len(parts) != 6 or parts[0].isdigit() is False:
                    return "Invalid trade link", 400
                if f.mode(int(parts[0])) != "DEMO":
                    return "DEMO mode is required. REAL mode is disabled.", 403
                html = original_trade()
                # Keep the broker page safe: the button goes to the official
                # Olymptrade demo landing page, not directly to the live platform.
                return str(html).replace(
                    "https://olymptrade.com/platform",
                    DEMO_LANDING,
                ).replace(
                    "OPEN OLYMPTRADE PLATFORM",
                    "OPEN OLYMPTRADE DEMO",
                ).replace(
                    "Signal parameters are shown here for manual entry. The official Olymptrade platform opens next.",
                    "DEMO ONLY. Asset, direction, entry and expiry shown above must be entered manually in Olymptrade. The bot does not control or place the broker order.",
                )
            safe_trade._DEMO_LOCKED = True
            flask.view_functions["final_trade_app"] = safe_trade

    f._DEMO_ONLY_LOCK = True
    if hasattr(a, "log"):
        a.log.warning("DEMO-ONLY LOCK ACTIVE: REAL mode disabled; manual broker action only")
    return True


def _boot():
    for _ in range(120):
        try:
            if _install():
                return
        except Exception:
            pass
        time.sleep(0.25)


threading.Thread(target=_boot, name="demo-only-lock", daemon=True).start()

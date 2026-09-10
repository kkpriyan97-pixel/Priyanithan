"""Trade Now UI asset-selection fix.

Shows the signal asset as an explicit selectable field before manual broker entry.
Never places an order or logs into Olymptrade.
"""
import html
import re
import sys
import threading
import time


def _app():
    m = sys.modules.get("app") or sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return None


def _install():
    a = _app()
    if a is None or getattr(a, "_TRADE_ASSET_SELECTION_HOTFIX", False):
        return bool(a)
    flask = getattr(a, "app", None)
    if flask is None:
        return False
    view = flask.view_functions.get("final_trade_app")
    if view is None or getattr(view, "_ASSET_SELECTION_WRAPPED", False):
        return False

    def wrapped_trade_app():
        result = view()
        if not isinstance(result, str) or "<html" not in result.lower():
            return result
        # Extract the already signed-token asset from the existing request.
        try:
            import fast_manual_entry as f
            raw = f.unpack(__import__("flask").request.args.get("token", ""))
            parts = raw.split("|") if raw else []
            pair = parts[1].strip() if len(parts) >= 6 else ""
        except Exception:
            pair = ""
        if not pair:
            return result
        safe_pair = html.escape(pair, quote=True)
        control = (
            "<div style='margin:14px 0;padding:14px;border:2px solid #444;border-radius:12px'>"
            "<div style='font-weight:700;margin-bottom:8px'>📈 ASSET SELECTION</div>"
            f"<select aria-label='Asset selection' style='width:100%;padding:12px;font-size:17px;border-radius:8px'>"
            f"<option selected value='{safe_pair}'>{safe_pair}</option></select>"
            "<div style='margin-top:8px;font-size:13px;opacity:.8'>Signal asset is pre-selected. Confirm the same asset manually on Olymptrade before entering the trade.</div>"
            "</div>"
        )
        if "ASSET SELECTION" in result:
            return result
        return re.sub(r"(<body[^>]*>)", r"\1" + control, result, count=1, flags=re.I)

    wrapped_trade_app._ASSET_SELECTION_WRAPPED = True
    flask.view_functions["final_trade_app"] = wrapped_trade_app
    a._TRADE_ASSET_SELECTION_HOTFIX = True
    if hasattr(a, "log"):
        a.log.warning("TRADE ASSET SELECTION ACTIVE: signal asset is explicitly shown and pre-selected")
    return True


def _boot():
    for _ in range(1800):
        try:
            if _install():
                return
        except Exception:
            pass
        time.sleep(0.1)

threading.Thread(target=_boot, name="trade-asset-selection", daemon=True).start()

"""Fresh 1-minute asset probe for Telegram asset selection.

Render runs app.py as __main__. Resolve that live module instead of importing a
second app.py copy, otherwise ot_client is isolated and every asset appears
"OlympTrade not connected". Full 40+ candle requirements remain reserved for
AI analysis. No broker order execution is enabled here.
"""
import asyncio
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
    if getattr(app, "_FRESH_ASSET_PROBE_V1", False):
        PATCHED = True
        return True

    async def fresh_probe(pair):
        client = app.ot_client
        if client is None or not client.connection.is_connected:
            return False, "OlympTrade not connected"
        try:
            raw = await client.market.get_candles(pair, 60, 20)
            df = app.normalize_candles(raw)
        except Exception as exc:
            return False, f"request failed: {exc}"
        if df is None or df.empty:
            return False, "no candle data"
        latest = float(df["timestamp"].iloc[-1])
        age = time.time() - latest
        if latest > time.time() + 120:
            return False, f"future candle rejected age={age:.1f}s"
        if age > app.LIVE_1M_MAX_AGE:
            return False, f"STALE candle rejected age={age:.1f}s"
        return True, None

    original = getattr(app, "verify_live_pair", None)
    if not callable(original):
        return False

    async def verify_live_pair(pair):
        ok, err = await fresh_probe(pair)
        if not ok:
            return False, err
        try:
            client = app.ot_client
            raw = await client.market.get_candles(pair, 60, 20)
            df = app.normalize_candles(raw)
            latest = float(df["timestamp"].iloc[-1])
            app.asset_cache[pair] = {"ts": latest, "checked": time.time()}
        except Exception as exc:
            return False, f"probe cache failed: {exc}"
        return True, None

    app.verify_live_pair = verify_live_pair
    app._FRESH_ASSET_PROBE_V1 = True
    try:
        app.log.warning("FRESH ASSET PROBE V1 ACTIVE: discovery accepts fresh 1m candle windows of 20 candles")
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


threading.Thread(target=_boot, name="fresh-asset-probe", daemon=True).start()

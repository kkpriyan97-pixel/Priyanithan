"""Fresh 1-minute asset probe for Telegram asset selection.

Asset discovery only needs to prove that the broker has a fresh 1-minute
candle. The full 40+ candle requirement remains reserved for AI analysis.
OlympTrade may return a smaller recent candle window, so discovery uses a
small direct request and strict timestamp freshness validation.
No broker order execution is enabled here.
"""
import asyncio
import threading
import time

PATCHED = False


def _patch():
    global PATCHED
    try:
        import app
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

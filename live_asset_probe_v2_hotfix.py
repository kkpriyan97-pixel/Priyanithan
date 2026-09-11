"""V2 live asset discovery diagnostics and broker-symbol fallback.

Resolve app.py as the running __main__ module. The old ``import app`` path could
create a second module with ot_client=None, producing false "not connected"
rejections while the real websocket was connected. V2 probes a bounded set of
broker candidates and logs exact rejection reasons. No broker order execution.
"""
import asyncio
import sys
import threading
import time

PATCHED = False
FALLBACK_PAIRS = ("_BRN", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "XAUUSD")


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
    if getattr(app, "_FRESH_ASSET_PROBE_V2", False):
        PATCHED = True
        return True

    async def probe(pair):
        client = app.ot_client
        if client is None or not client.connection.is_connected:
            return False, "OlympTrade not connected"
        try:
            raw = await client.market.get_candles(pair, 60, 20)
            df = app.normalize_candles(raw)
        except Exception as exc:
            return False, f"request failed: {str(exc)[:160]}"
        if df is None or df.empty:
            return False, "no candle data"
        latest = float(df["timestamp"].iloc[-1])
        age = time.time() - latest
        if latest > time.time() + 120:
            return False, f"future candle age={age:.1f}s"
        if age > app.LIVE_1M_MAX_AGE:
            return False, f"stale candle age={age:.1f}s"
        close = float(df["close"].iloc[-1])
        if close <= 0:
            return False, "invalid close price"
        app.asset_cache[pair] = {"ts": latest, "checked": time.time()}
        return True, None

    async def verify_live_pair(pair):
        return await probe(pair)

    async def discover_live_assets():
        candidates = set(getattr(app, "broker_catalog", set()))
        candidates.update(getattr(app, "ENV_PAIRS", []))
        if not candidates:
            candidates.update(getattr(app, "SEED_PAIRS", []))
        candidates.update(FALLBACK_PAIRS)
        candidates = sorted(str(p).strip().upper() for p in candidates if str(p).strip())
        live = []
        failures = []
        sem = asyncio.Semaphore(3)

        async def one(pair):
            async with sem:
                ok, err = await probe(pair)
                return pair, ok, err

        results = await asyncio.gather(*(one(p) for p in candidates), return_exceptions=True)
        for item in results:
            if isinstance(item, Exception):
                failures.append(f"<exception>: {str(item)[:120]}")
                continue
            pair, ok, err = item
            if ok:
                live.append(pair)
            elif len(failures) < 10:
                failures.append(f"{pair}: {err or 'rejected'}")

        app.log.warning(
            "LIVE ASSET DISCOVERY V2: candidates=%s live=%s rejected=%s",
            len(candidates), len(live), len(candidates) - len(live),
        )
        if failures:
            app.log.warning("LIVE ASSET DISCOVERY V2 REASONS: %s", " | ".join(failures))
        return sorted(set(live))

    app.verify_live_pair = verify_live_pair
    app.discover_live_assets = discover_live_assets
    app._FRESH_ASSET_PROBE_V2 = True
    try:
        app.log.warning("FRESH ASSET PROBE V2 ACTIVE: running-app state + broker fallback + rejection diagnostics")
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


threading.Thread(target=_boot, name="fresh-asset-probe-v2", daemon=True).start()

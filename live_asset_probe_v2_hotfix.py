"""V2 live asset discovery diagnostics and broker-symbol fallback.

The previous probe showed candidates=28/live=0 but hid every rejection reason.
V2 adds the historically working broker symbol _BRN as a fallback candidate,
probes sequentially to avoid flooding the candle endpoint, and logs a bounded
set of exact rejection reasons. Only a genuinely fresh 1-minute candle is
accepted. No broker order execution is enabled.
"""
import asyncio
import threading
import time

PATCHED = False

FALLBACK_PAIRS = ("_BRN", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "XAUUSD")


def _patch():
    global PATCHED
    try:
        import app
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
        # Keep a known broker-native fallback in case instrument event 1054 is
        # not delivered on this websocket session.
        candidates.update(FALLBACK_PAIRS)
        candidates = sorted(str(p).strip().upper() for p in candidates if str(p).strip())
        live = []
        failures = []
        # Small concurrency protects the broker endpoint from a 28-request burst.
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
        app.log.warning("FRESH ASSET PROBE V2 ACTIVE: broker fallback + rejection diagnostics + bounded candle concurrency")
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

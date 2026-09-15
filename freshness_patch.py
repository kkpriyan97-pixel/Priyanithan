"""Runtime freshness bridge plus indicator telemetry repair for Candice."""
from __future__ import annotations
import logging
import sys
import threading
import time

LOG = logging.getLogger("candice.freshness")


def _ts(value):
    try:
        x = float(value or 0)
        if x > 10_000_000_000:
            x /= 1000.0
        return x if x > 0 else 0.0
    except Exception:
        return 0.0


def _age(candle, asset=None):
    now = time.time()
    sc = sys.modules.get("sitecustomize")
    if sc is not None and asset:
        try:
            with getattr(sc, "LOCK"):
                rec = (getattr(sc, "LIVE_RECEIPTS", {}) or {}).get(asset)
            if rec and float(rec[1]) <= now + 5:
                return max(0.0, now - float(rec[1]))
        except Exception:
            pass
    try:
        received = float((candle or {}).get("received_at", 0) or 0)
        if 0 < received <= now + 5:
            return max(0.0, now - received)
    except Exception:
        pass
    ts = _ts((candle or {}).get("timestamp", (candle or {}).get("time", 0)))
    if ts > 0:
        return max(0.0, now - ts)
    return 1e9


def install():
    sc = sys.modules.get("sitecustomize")
    if sc is None:
        return False
    sc.candle_age = _age
    return True


def _close(v):
    try:
        return float(v.get("close", 0))
    except Exception:
        return 0.0


def _aggregate(data, minutes):
    """Aggregate completed 1m candles into deterministic 3m/5m bars."""
    out = []
    if not data:
        return out
    buckets = {}
    for c in data:
        ts = _ts(c.get("timestamp", c.get("time", 0)))
        if ts <= 0:
            continue
        bucket = int(ts // (minutes * 60))
        buckets.setdefault(bucket, []).append(c)
    for bucket in sorted(buckets):
        rows = buckets[bucket]
        if len(rows) < minutes:
            continue
        out.append({
            "open": float(rows[0].get("open", _close(rows[0]))),
            "high": max(float(x.get("high", _close(x))) for x in rows),
            "low": min(float(x.get("low", _close(x))) for x in rows),
            "close": _close(rows[-1]),
            "timestamp": float(bucket * minutes * 60),
        })
    return out


def _ema(values, period):
    if len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    e = sum(values[:period]) / period
    for x in values[period:]:
        e = x * k + e * (1.0 - k)
    return e


def _rsi(values, period=14):
    if len(values) <= period:
        return None
    gains = losses = 0.0
    for a, b in zip(values[-period-1:-1], values[-period:]):
        d = b - a
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def _adx(data, period=14):
    if len(data) < period + 2:
        return None
    trs, plus_dm, minus_dm = [], [], []
    for prev, cur in zip(data[-period-1:-1], data[-period:]):
        hi = float(cur.get("high", _close(cur)))
        lo = float(cur.get("low", _close(cur)))
        ph = float(prev.get("high", _close(prev)))
        pl = float(prev.get("low", _close(prev)))
        pc = _close(prev)
        trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
        up = hi - ph
        down = pl - lo
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    atr = sum(trs) / period
    if atr <= 0:
        return 0.0
    pdi = 100.0 * (sum(plus_dm) / period) / atr
    mdi = 100.0 * (sum(minus_dm) / period) / atr
    den = pdi + mdi
    return 0.0 if den <= 0 else 100.0 * abs(pdi - mdi) / den


def _trend(data):
    closes = [_close(x) for x in data]
    e9 = _ema(closes, 9)
    e21 = _ema(closes, 21)
    if e9 is None or e21 is None:
        return 0
    if e9 > e21:
        return 1
    if e9 < e21:
        return -1
    return 0


def _telemetry_brain(data, asset):
    """Run the real strategy brain while supplying real MTF and indicator telemetry."""
    from strategy_brain import evaluate as brain_evaluate
    bars3 = _aggregate(data, 3)
    bars5 = _aggregate(data, 5)
    mtf = {"1m": _trend(data), "3m": _trend(bars3), "5m": _trend(bars5)}
    b = brain_evaluate(asset, data, {"mtf": mtf})
    closes = [_close(x) for x in data]
    rsi = _rsi(closes)
    adx = _adx(data)
    # Telegram's formatter in app.py consumes these exact uppercase keys.
    # Keep lowercase aliases too so other telemetry consumers remain compatible.
    indicators = {
        "RSI": round(rsi, 2) if rsi is not None else None,
        "ADX": round(adx, 2) if adx is not None else None,
        "rsi": round(rsi, 2) if rsi is not None else None,
        "adx": round(adx, 2) if adx is not None else None,
    }
    if b.get("allow"):
        return {"decision": "SIGNAL", "direction": b["direction"], "confidence": b["score"], "reasons": b.get("reasons", []), "brain": b, "mtf": mtf, "indicators": indicators}
    return {"decision": "NO_SIGNAL", "direction": b.get("direction"), "confidence": b.get("score", 0), "reasons": b.get("reasons", []), "brain": b, "mtf": mtf, "indicators": indicators}


def _set_cell(fn, name, value):
    """Replace a closure cell value without changing the original runtime overlay."""
    import ctypes
    for i, freevar in enumerate(getattr(fn.__code__, "co_freevars", ())):
        if freevar == name:
            ctypes.pythonapi.PyCell_Set.argtypes = [ctypes.py_object, ctypes.py_object]
            ctypes.pythonapi.PyCell_Set.restype = ctypes.c_int
            return ctypes.pythonapi.PyCell_Set(fn.__closure__[i], value) == 0
    return False


def _patch_indicator_runtime():
    """Patch both app analyze and the running scheduler's captured brain function."""
    patched = False
    for _ in range(180):
        try:
            sc = sys.modules.get("sitecustomize")
            main = sys.modules.get("__main__")
            if sc is not None and main is not None and getattr(sc, "INSTALLED", False):
                def live_brain(data):
                    asset = getattr(threading.current_thread(), "candice_asset", None) or "UNKNOWN"
                    return _telemetry_brain(data, asset)
                main.analyze = live_brain
                for t in threading.enumerate():
                    if t.name == "candice-brain-scheduler":
                        target = getattr(t, "_target", None)
                        if target is not None:
                            _set_cell(target, "brain_analyze", live_brain)
                            patched = True
                            LOG.info("CANDICE_INDICATOR_TELEMETRY patched | MTF=1m/3m/5m | RSI=14 | ADX=14 | telegram_keys=RSI/ADX")
                        break
                if patched:
                    return
        except Exception:
            LOG.exception("CANDICE_INDICATOR_TELEMETRY patch failed")
        time.sleep(0.5)
    LOG.error("CANDICE_INDICATOR_TELEMETRY could not patch live scheduler")


def _late_install():
    found = False
    for _ in range(80):
        try:
            sc = sys.modules.get("sitecustomize")
            if sc is not None and getattr(sc, "candle_age", None) is not _age:
                sc.candle_age = _age
                found = True
                LOG.info("CANDICE_FRESHNESS_BRIDGE installed | receipt -> received_at -> candle_timestamp fallback")
            elif sc is not None:
                found = True
        except Exception:
            LOG.exception("Freshness bridge install failed")
        time.sleep(0.1)
    if not found:
        LOG.error("CANDICE_FRESHNESS_BRIDGE failed to find sitecustomize")
    else:
        LOG.info("CANDICE_FRESHNESS_BRIDGE startup enforcement complete")
    _patch_indicator_runtime()


threading.Thread(target=_late_install, name="candice-freshness-bridge", daemon=True).start()

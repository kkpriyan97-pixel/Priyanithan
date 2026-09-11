"""Conservative market-condition duration selector for Candice signals.

This hotfix changes only the signal expiry choice. It never creates a signal,
weakens approval gates, places broker orders, or changes exact result checking.
"""
import sys
import time

ALLOWED_DURATIONS = (2, 3, 5, 10, 15)
PATCHED = False


def _app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    return sys.modules.get("app")


def _metrics(df):
    closes = [float(x) for x in df["close"].tail(12)]
    opens = [float(x) for x in df["open"].tail(12)]
    highs = [float(x) for x in df["high"].tail(12)]
    lows = [float(x) for x in df["low"].tail(12)]
    if len(closes) < 8:
        return None
    base = max(abs(closes[0]), 1e-12)
    momentum = (closes[-1] - closes[0]) / base * 10000.0
    ranges = [(h - l) / max(abs(c), 1e-12) * 10000.0 for h, l, c in zip(highs, lows, closes)]
    bodies = [abs(c - o) / max(h - l, 1e-12) for c, o, h, l in zip(closes, opens, highs, lows)]
    return {
        "momentum": momentum,
        "avg_range": sum(ranges) / len(ranges),
        "body_ratio": sum(bodies) / len(bodies),
    }


def _choose(signal, df1, df5):
    m1 = _metrics(df1)
    m5 = _metrics(df5)
    if not m1 or not m5:
        return 5, "insufficient duration metrics; conservative 5 MIN"

    direction = str(signal.get("direction", "")).upper()
    trend5 = str(signal.get("trend_5m", "")).upper()
    aligned = direction == trend5
    confidence = float(signal.get("ai_confidence", signal.get("confidence", 0)) or 0)
    m1_dir = "UP" if m1["momentum"] > 0 else "DOWN" if m1["momentum"] < 0 else "FLAT"
    m5_dir = "UP" if m5["momentum"] > 0 else "DOWN" if m5["momentum"] < 0 else "FLAT"
    micro_aligned = m1_dir == direction
    macro_aligned = m5_dir == direction

    # Very fast movement with a strong body favors a short expiry: less time
    # exposed to a possible micro reversal.
    if micro_aligned and abs(m1["momentum"]) >= 10 and m1["body_ratio"] >= 0.62:
        return (2 if abs(m1["momentum"]) >= 18 else 3), "strong 1m momentum/body -> short expiry"

    # Broad agreement with calm structure can support a longer expiry.
    if aligned and macro_aligned and confidence >= 86:
        if m1["avg_range"] <= 18 and m1["body_ratio"] >= 0.48:
            return 15, "strong 1m/5m alignment with stable structure -> 15 MIN"
        if m1["avg_range"] <= 30:
            return 10, "strong 1m/5m alignment -> 10 MIN"

    # Normal aligned setups use the middle duration.
    if aligned and micro_aligned and macro_aligned:
        return 5, "multi-timeframe alignment -> 5 MIN"

    # If the higher timeframe agrees but the 1m is less decisive, use a
    # shorter window rather than extending exposure through uncertainty.
    if aligned and macro_aligned:
        return 3, "5m alignment with weaker micro structure -> 3 MIN"

    # Conflicting structure is already subject to Candice's approval gates;
    # when it survives, keep expiry short and conservative.
    return 2, "mixed timeframe structure -> conservative 2 MIN"


async def _patched_analyze(module, pair):
    original = getattr(module, "_DURATION_ORIGINAL_ANALYZE", None)
    if not callable(original):
        return None, "duration selector original analyzer unavailable"
    signal, err = await original(pair)
    if signal is None:
        return None, err
    duration = int(signal.get("duration", 5) or 5)
    if duration not in ALLOWED_DURATIONS:
        duration = 5
    try:
        df1, err1 = await module.get_candles(pair, 60, 40, module.LIVE_1M_MAX_AGE)
        df5, err5 = await module.get_candles(pair, 300, 40, module.LIVE_5M_MAX_AGE)
        if df1 is not None and df5 is not None:
            duration, reason = _choose(signal, df1, df5)
        else:
            reason = f"duration metrics unavailable ({err1 or err5}); conservative {duration} MIN"
    except Exception as exc:
        reason = f"duration metrics failed; conservative {duration} MIN: {exc}"

    signal["duration"] = int(duration)
    signal["duration_reason"] = reason
    signal["duration_choices"] = list(ALLOWED_DURATIONS)
    module.log.info(
        "CANDICE DURATION SELECTED: pair=%s duration=%s MIN confidence=%s reason=%s",
        pair, duration, signal.get("ai_confidence"), reason,
    )
    return signal, None


def _patch(module):
    global PATCHED
    if getattr(module, "_CANDICE_DURATION_SELECTION_V1", False):
        PATCHED = True
        return True
    analyze = getattr(module, "analyze_asset", None)
    if not callable(analyze):
        return False
    module._DURATION_ORIGINAL_ANALYZE = analyze
    module.analyze_asset = lambda pair: _patched_analyze(module, pair)
    module._CANDICE_DURATION_SELECTION_V1 = True
    module.log.warning("CANDICE DURATION SELECTION V1 ACTIVE: 2/3/5/10/15 MIN market-condition choice")
    PATCHED = True
    return True


def _boot():
    for _ in range(1800):
        try:
            module = _app()
            if module is not None and _patch(module):
                return
        except Exception:
            pass
        time.sleep(0.1)

import threading
threading.Thread(target=_boot, name="candice-duration-selection-boot", daemon=True).start()

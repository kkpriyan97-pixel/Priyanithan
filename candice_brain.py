"""Candice live technical brain. Read-only; never places orders."""
from __future__ import annotations

import time
from math import isfinite

EXPIRIES = (1, 2, 3, 4, 5, 10, 15)
MIN_CLOSED_CANDLES = 30
ONE_MINUTE = 60
FIFTEEN_MINUTES = 900
CLOSE_GRACE_SECONDS = 1


def _f(x, d=0.0):
    try:
        v = float(x)
        return v if isfinite(v) else d
    except Exception:
        return d


def _ts(x, d=0.0):
    try:
        v = float(x)
        if v > 20_000_000_000:
            v /= 1000.0
        return v if isfinite(v) else d
    except Exception:
        return d


def _norm(c):
    return {
        "time": c.get("time", c.get("t")),
        "open": _f(c.get("open", c.get("o"))),
        "high": _f(c.get("high", c.get("h"))),
        "low": _f(c.get("low", c.get("l"))),
        "close": _f(c.get("close", c.get("c"))),
        "volume": _f(c.get("volume", c.get("v"))),
    }


def _prepare_closed_1m(candles, now=None):
    """Normalize, sort and keep only fully closed 1-minute candles."""
    now = time.time() if now is None else float(now)
    by_minute = {}
    for raw in candles or []:
        if not isinstance(raw, dict):
            continue
        c = _norm(raw)
        ts = _ts(c["time"], -1)
        if ts < 0:
            continue
        minute = int(ts // ONE_MINUTE) * ONE_MINUTE
        c["time"] = minute
        if minute + ONE_MINUTE > now - CLOSE_GRACE_SECONDS:
            continue
        by_minute[minute] = c
    return [by_minute[k] for k in sorted(by_minute)]


def _aggregate_closed_15m(closed_1m, now=None):
    """Aggregate complete wall-clock 15-minute blocks only."""
    now = time.time() if now is None else float(now)
    groups = {}
    for c in closed_1m:
        ts = int(c["time"])
        bucket = (ts // FIFTEEN_MINUTES) * FIFTEEN_MINUTES
        if bucket + FIFTEEN_MINUTES > now - CLOSE_GRACE_SECONDS:
            continue
        groups.setdefault(bucket, []).append(c)

    out = []
    for bucket in sorted(groups):
        bars = sorted(groups[bucket], key=lambda x: x["time"])
        expected = [bucket + i * ONE_MINUTE for i in range(15)]
        if len(bars) != 15 or [int(x["time"]) for x in bars] != expected:
            continue
        out.append(
            {
                "time": bucket,
                "open": bars[0]["open"],
                "high": max(x["high"] for x in bars),
                "low": min(x["low"] for x in bars),
                "close": bars[-1]["close"],
                "volume": sum(x.get("volume", 0.0) for x in bars),
            }
        )
    return out


def ema(v, n):
    if not v:
        return 0.0
    k = 2 / (n + 1)
    e = v[0]
    for x in v[1:]:
        e = x * k + e * (1 - k)
    return e


def rsi(v, n=14):
    if len(v) < n + 1:
        return 50.0
    gains = []
    losses = []
    for a, b in zip(v[-n-1:-1], v[-n:]):
        d = b - a
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains) / n
    avg_loss = sum(losses) / n
    return 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))


def atr(cs, n=14):
    if len(cs) < 2:
        return 0.0
    trs = []
    for a, b in zip(cs[-n-1:-1], cs[-n:]):
        trs.append(
            max(
                b["high"] - b["low"],
                abs(b["high"] - a["close"]),
                abs(b["low"] - a["close"]),
            )
        )
    return sum(trs) / len(trs) if trs else 0.0


def analyze_asset(asset, candles, price=None, now=None):
    now = time.time() if now is None else float(now)
    cs = _prepare_closed_1m(candles, now)
    if len(cs) < MIN_CLOSED_CANDLES:
        return None

    blocks_15m = _aggregate_closed_15m(cs, now)
    if len(blocks_15m) < 2:
        return None

    values = [c["close"] for c in cs]
    last = cs[-1]
    p = _f(price, last["close"])

    e9 = ema(values[-30:], 9)
    e21 = ema(values[-30:], 21)
    rr = rsi(values)
    aa = atr(cs)

    body = last["close"] - last["open"]
    rng = max(last["high"] - last["low"], 1e-12)
    upper = last["high"] - max(last["open"], last["close"])
    lower = min(last["open"], last["close"]) - last["low"]

    s1 = (
        "BULLISH"
        if e9 > e21 and rr >= 52 and p >= e9
        else "BEARISH"
        if e9 < e21 and rr <= 48 and p <= e9
        else "MIXED"
    )

    prev_15m = blocks_15m[-2]
    last_15m = blocks_15m[-1]
    trend = (
        "UP"
        if last_15m["close"] > prev_15m["close"]
        and last_15m["high"] >= prev_15m["high"]
        and last_15m["low"] >= prev_15m["low"]
        else "DOWN"
        if last_15m["close"] < prev_15m["close"]
        and last_15m["low"] <= prev_15m["low"]
        and last_15m["high"] <= prev_15m["high"]
        else "SIDEWAYS"
    )

    pattern = (
        "BULLISH_CANDLE"
        if body > 0 and body / rng >= 0.55
        else "BEARISH_CANDLE"
        if body < 0 and -body / rng >= 0.55
        else "PIN_REJECTION"
        if max(upper, lower) / rng > 0.45
        else "NEUTRAL"
    )

    direction = None
    strategy = ""

    if trend == "UP" and s1 == "BULLISH" and (
        pattern == "BULLISH_CANDLE" or p > e9
    ):
        direction = "UP"
        strategy = "TREND_FOLLOWING"
    elif trend == "DOWN" and s1 == "BEARISH" and (
        pattern == "BEARISH_CANDLE" or p < e9
    ):
        direction = "DOWN"
        strategy = "TREND_FOLLOWING"
    elif trend == "UP" and lower > abs(body) * 1.2 and p >= e21:
        direction = "UP"
        strategy = "PULLBACK"
    elif trend == "DOWN" and upper > abs(body) * 1.2 and p <= e21:
        direction = "DOWN"
        strategy = "PULLBACK"
    else:
        # Breakout is evaluated against the prior 30 CLOSED candles.
        prior = cs[-31:-1]
        prior_hi = max(c["high"] for c in prior) if prior else 0.0
        prior_lo = min(c["low"] for c in prior) if prior else 0.0
        if (
            strategy == ""
            and prior
            and aa > 0
            and last["close"] > prior_hi
            and body > 0
            and body / rng >= 0.55
        ):
            direction = "UP"
            strategy = "BREAKOUT"
        elif (
            strategy == ""
            and prior
            and aa > 0
            and last["close"] < prior_lo
            and body < 0
            and -body / rng >= 0.55
        ):
            direction = "DOWN"
            strategy = "BREAKOUT"
        elif rr < 30 and body > 0:
            direction = "UP"
            strategy = "MEAN_REVERSION"
        elif rr > 70 and body < 0:
            direction = "DOWN"
            strategy = "MEAN_REVERSION"

    if not direction or trend == "SIDEWAYS" or s1 == "MIXED":
        return None

    alignment = 25
    momentum = min(20, abs(rr - 50) * 0.45)
    candle = min(15, abs(body) / rng * 15)
    vol = 10 if aa > 0 else 5
    quality = alignment + momentum + candle + vol + min(
        15, max(0, abs(p - e21) / (aa or 1) * 5)
    )
    confidence = min(99, int(70 + quality * 0.28))
    if confidence < 90:
        return None

    expiry = 5
    if strategy == "PULLBACK":
        expiry = 2
    elif strategy == "BREAKOUT":
        expiry = 1
    elif abs(rr - 50) > 15:
        expiry = 3

    return {
        "pair": str(asset.get("pair", "")),
        "display_name": str(
            asset.get("display_name") or asset.get("title") or ""
        ),
        "direction": direction,
        "confidence": confidence,
        "strategy": strategy,
        "expiry_minutes": expiry,
        "pattern": pattern,
        "trend_15m": trend,
        "structure_1m": s1,
        "market_quality": round(quality, 2),
        "reason": (
            f"{strategy}: {pattern}; 15m={trend}; 1m={s1}; "
            f"RSI={rr:.1f}; EMA9/21 aligned; closed-candle basis."
        ),
        "entry_candle_ts": last["time"],
        "price": p,
        "decision_candle_closed": True,
        "closed_1m_ts": last["time"],
        "closed_15m_ts": last_15m["time"],
    }

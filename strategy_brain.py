from __future__ import annotations

from collections import defaultdict, deque
from math import isfinite

# Read-only decision layer. It does not place orders.

STATE = defaultdict(lambda: {
    "last_regime": None,
    "last_direction": None,
    "last_setup_key": None,
    "cooldown": 0,
    "strategy_scores": defaultdict(lambda: {"samples": 0, "wins": 0, "losses": 0}),
})


def _f(x):
    try:
        x = float(x)
        return x if isfinite(x) else None
    except Exception:
        return None


def _ema(values, period):
    if len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    z = sum(values[:period]) / period
    for x in values[period:]:
        z = x * k + z * (1.0 - k)
    return z


def _rsi(values, period=14):
    if len(values) <= period:
        return None
    gains = losses = 0.0
    for a, b in zip(values[-period-1:-1], values[-period:]):
        d = b - a
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    gains /= period
    losses /= period
    return 100.0 if losses == 0 else 100.0 - 100.0 / (1.0 + gains / losses)


def _atr(data, period=14):
    if len(data) <= period:
        return None
    tr = []
    for a, b in zip(data[-period-1:-1], data[-period:]):
        tr.append(max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"])))
    return sum(tr) / period


def _slope(values, n=12):
    if len(values) < n + 1:
        return 0.0
    return (values[-1] - values[-1-n]) / n


def _regime(data):
    closes = [x["close"] for x in data]
    e9, e21, e50 = _ema(closes, 9), _ema(closes, 21), _ema(closes, 50)
    atr = _atr(data)
    slope = _slope(closes, 15)
    if not all(v is not None for v in (e9, e21, e50, atr)):
        return "WARMUP", 0.0
    recent_hi = max(x["high"] for x in data[-30:])
    recent_lo = min(x["low"] for x in data[-30:])
    width = recent_hi - recent_lo
    trend_gap = abs(e9 - e21)
    if width <= max(atr * 4.0, 1e-12) and trend_gap <= atr * 0.65:
        return "RANGE", width
    if abs(slope) > atr * 0.10 and abs(e9-e21) > atr * 0.35:
        return "TREND_UP" if slope > 0 and e9 > e21 else "TREND_DOWN" if slope < 0 and e9 < e21 else "TRANSITION", abs(slope)
    # Breakout: current candle closes outside the prior 30-candle structure.
    prior_hi = max(x["high"] for x in data[-31:-1])
    prior_lo = min(x["low"] for x in data[-31:-1])
    last = data[-1]
    if last["close"] > prior_hi:
        return "BREAKOUT_UP", last["close"] - prior_hi
    if last["close"] < prior_lo:
        return "BREAKOUT_DOWN", prior_lo - last["close"]
    return "TRANSITION", abs(slope)


def _candle_score(data):
    if len(data) < 3:
        return 0, "none"
    a, b = data[-2], data[-1]
    body = abs(b["close"] - b["open"])
    rng = max(b["high"] - b["low"], 1e-12)
    upper = b["high"] - max(b["open"], b["close"])
    lower = min(b["open"], b["close"]) - b["low"]
    if body / rng < .28 and lower / rng > .55:
        return 2, "hammer_rejection"
    if body / rng < .28 and upper / rng > .55:
        return -2, "shooting_star_rejection"
    if b["close"] > b["open"] and a["close"] < a["open"] and b["close"] >= a["open"] and b["open"] <= a["close"]:
        return 3, "bullish_engulfing"
    if b["close"] < b["open"] and a["close"] > a["open"] and b["open"] >= a["close"] and b["close"] <= a["open"]:
        return -3, "bearish_engulfing"
    return 1 if b["close"] > a["close"] else -1 if b["close"] < a["close"] else 0, "momentum_candle"


def evaluate(asset, data, base):
    """Return a strategy-brain decision layered over the technical engine.

    The brain deliberately prefers NO_SIGNAL when strategies disagree. It is
    an analyst gate, not an order executor.
    """
    if len(data) < 80:
        return {"allow": False, "regime": "WARMUP", "strategy": "warmup", "score": 0, "reasons": ["brain warming"]}

    closes = [x["close"] for x in data]
    e9, e21, e50 = _ema(closes, 9), _ema(closes, 21), _ema(closes, 50)
    r = _rsi(closes)
    atr = _atr(data)
    regime, regime_strength = _regime(data)
    candle, pattern = _candle_score(data)
    direction = 1 if base.get("direction") == "UP" else -1 if base.get("direction") == "DOWN" else 0
    mtf = base.get("mtf") or {}
    s1, s3, s5 = float(mtf.get("1m", 0) or 0), float(mtf.get("3m", 0) or 0), float(mtf.get("5m", 0) or 0)

    votes = {}
    # Trend-following strategy.
    trend_vote = 0
    if e9 and e21 and e50:
        if e9 > e21 > e50: trend_vote = 1
        elif e9 < e21 < e50: trend_vote = -1
    votes["TREND_FOLLOW"] = trend_vote

    # Momentum strategy.
    momentum_vote = 1 if s1 >= 5 and s3 >= 0 and s5 >= 0 else -1 if s1 <= -5 and s3 <= 0 and s5 <= 0 else 0
    if r is not None and 45 <= r <= 70 and momentum_vote < 0: momentum_vote = 0
    if r is not None and 30 <= r <= 55 and momentum_vote > 0: momentum_vote = 0
    votes["MOMENTUM"] = momentum_vote

    # Mean-reversion only in genuine ranges.
    last = closes[-1]
    hi, lo = max(x["high"] for x in data[-30:]), min(x["low"] for x in data[-30:])
    mean_vote = 0
    if regime == "RANGE" and hi > lo:
        pos = (last - lo) / (hi - lo)
        if pos < .18 and candle > 0: mean_vote = 1
        elif pos > .82 and candle < 0: mean_vote = -1
    votes["RANGE_REVERSION"] = mean_vote

    # Breakout strategy requires a clean close and directional confirmation.
    breakout_vote = 1 if regime == "BREAKOUT_UP" and candle > 0 and s1 > 0 else -1 if regime == "BREAKOUT_DOWN" and candle < 0 and s1 < 0 else 0
    votes["BREAKOUT"] = breakout_vote

    # Reversal strategy is intentionally conservative: rejection + extreme RSI.
    reversal_vote = 0
    if r is not None:
        if r <= 25 and candle > 0: reversal_vote = 1
        elif r >= 75 and candle < 0: reversal_vote = -1
    votes["REVERSAL"] = reversal_vote

    aligned = [v for v in votes.values() if v]
    agreement = sum(1 for v in aligned if v == direction)
    opposition = sum(1 for v in aligned if v == -direction)

    # Regime-specific strategy priority.
    preferred = {
        "TREND_UP": "TREND_FOLLOW", "TREND_DOWN": "TREND_FOLLOW",
        "BREAKOUT_UP": "BREAKOUT", "BREAKOUT_DOWN": "BREAKOUT",
        "RANGE": "RANGE_REVERSION",
    }.get(regime, "MOMENTUM")
    preferred_vote = votes.get(preferred, 0)

    # Strong disagreement is a hard NO_SIGNAL.
    if direction == 0 or opposition >= 2 or preferred_vote not in (0, direction):
        return {"allow": False, "regime": regime, "strategy": preferred, "score": 0,
                "reasons": [f"brain reject: regime={regime}", f"strategies={votes}", "strategy disagreement"]}

    # Need multiple independent confirmations; one indicator cannot create a signal.
    if agreement < 2:
        return {"allow": False, "regime": regime, "strategy": preferred, "score": agreement,
                "reasons": [f"brain reject: only {agreement} strategy confirmations", f"regime={regime}"]}

    quality = min(100, 52 + agreement * 9 + min(18, abs(s1) * 1.5) + (8 if s3 * direction > 0 else 0) + (8 if s5 * direction > 0 else 0))
    if regime_strength and atr and regime in ("TREND_UP", "TREND_DOWN"):
        quality += min(5, abs(regime_strength) / max(atr, 1e-12))
    quality = int(min(95, quality))
    if quality < 72:
        return {"allow": False, "regime": regime, "strategy": preferred, "score": quality,
                "reasons": [f"brain reject: quality {quality}% < 72%", f"regime={regime}"]}

    setup_key = f"{regime}:{preferred}:{'UP' if direction > 0 else 'DOWN'}:{round(last / max(atr or 1, 1e-12), 1)}"
    state = STATE[asset]
    if state["last_setup_key"] == setup_key and state["last_direction"] == direction:
        return {"allow": False, "regime": regime, "strategy": preferred, "score": quality,
                "reasons": ["same setup remains active; waiting for a new setup"]}

    state["last_regime"] = regime
    state["last_direction"] = direction
    state["last_setup_key"] = setup_key
    return {"allow": True, "regime": regime, "strategy": preferred, "score": quality,
            "reasons": [f"OWN BRAIN: {preferred}", f"regime={regime}", f"strategy confirmations={agreement}", f"brain quality={quality}%", f"pattern={pattern}"]}

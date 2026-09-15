"""Candice Brain training/ranking layer.

This module teaches the Brain the indicator/strategy families visible in the
reference signals.  It never places trades.  Live price structure remains the
primary signal source; these methods are confirmations and the historical
outcome database decides which combinations deserve more weight over time.
"""
from __future__ import annotations
from collections import defaultdict
import json, math, os, sqlite3, time

DB_PATH = os.getenv("CANDICE_OUTCOME_DB", "/tmp/candice_outcomes.sqlite3")

# Reference strategy families from the supplied VIP signals + the Brain's
# existing price-action families.
METHODS = (
    "trend_following",
    "breakout",
    "pullback",
    "support_resistance",
    "candlestick",
    "momentum",
    "mean_reversion",
    "reversal",
    "multi_timeframe",
    "volatility",
    "market_structure",
    "price_action",
    # Supplied reference strategy.
    "moving_average_crossover",
    "demarker_overbought_oversold",
    "stochastic_overbought_oversold",
    "macd_crossover",
    "rsi_overbought_oversold",
    "bollinger_bands_breakout",
    "parabolic_sar_reversal",
    "rate_of_change_crossover",
    "donchian_channel_breakout",
)


def _rows(limit: int = 500):
    try:
        c = sqlite3.connect(DB_PATH, timeout=2)
        rows = c.execute(
            'SELECT asset,direction,expiry,confidence,strategy,regime,pattern,result,feature_json '
            'FROM signals WHERE status="EVALUATED" ORDER BY id DESC LIMIT ?',
            (limit,),
        ).fetchall()
        c.close()
        return rows
    except Exception:
        return []


def train_snapshot():
    """Build historical setup rankings from completed outcomes."""
    rows = _rows()
    by = defaultdict(lambda: [0, 0, 0])
    for asset, direction, expiry, conf, strategy, regime, pattern, result, features in rows:
        k = (str(asset).upper(), str(direction), int(expiry), str(regime), str(pattern))
        by[k][0] += 1
        by[k][1] += result == "WIN"
        by[k][2] += result == "LOSS"

    ranked = []
    for k, (n, w, l) in by.items():
        if n < 2:
            continue
        ranked.append(
            {
                "key": k,
                "samples": n,
                "wins": w,
                "losses": l,
                "win_rate": round(100 * w / (w + l), 1) if w + l else 0,
            }
        )
    ranked.sort(key=lambda x: (x["win_rate"], x["samples"]), reverse=True)
    return {
        "evaluated": len(rows),
        "methods": list(METHODS),
        "ranked_setups": ranked[:50],
        "trained_at": time.time(),
    }


def score_setup(asset, direction, expiry, regime, pattern):
    s = train_snapshot()
    key = (str(asset).upper(), str(direction), int(expiry), str(regime), str(pattern))
    for x in s["ranked_setups"]:
        if x["key"] == key:
            return x["win_rate"], x["samples"]
    return 50.0, 0


def _ema(values, period):
    if len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    value = sum(values[:period]) / period
    for x in values[period:]:
        value = x * k + value * (1.0 - k)
    return value


def _rsi(values, period=14):
    if len(values) <= period:
        return None
    gains = losses = 0.0
    for a, b in zip(values[-period - 1:-1], values[-period:]):
        d = b - a
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def _stochastic(data, period=14):
    if len(data) < period:
        return None
    recent = data[-period:]
    high = max(float(x["high"]) for x in recent)
    low = min(float(x["low"]) for x in recent)
    if high <= low:
        return 50.0
    return 100.0 * (float(recent[-1]["close"]) - low) / (high - low)


def _atr(data, period=14):
    if len(data) <= period:
        return None
    trs = []
    for a, b in zip(data[-period - 1:-1], data[-period:]):
        hi = float(b["high"]); lo = float(b["low"]); pc = float(a["close"])
        trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
    return sum(trs) / len(trs)


def _macd(values):
    e12 = _ema(values, 12)
    e26 = _ema(values, 26)
    if e12 is None or e26 is None:
        return None
    return e12 - e26


def _bollinger(values, period=20, mult=2.0):
    if len(values) < period:
        return None
    r = values[-period:]
    mean = sum(r) / period
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / period)
    return mean, mean + mult * sd, mean - mult * sd


def _donchian(data, period=20):
    if len(data) < period + 1:
        return None
    prior = data[-period - 1:-1]
    return max(float(x["high"]) for x in prior), min(float(x["low"]) for x in prior)


def _roc(values, period=9):
    if len(values) <= period:
        return None
    base = values[-period - 1]
    if base == 0:
        return None
    return (values[-1] - base) / abs(base) * 100.0


def _demarker(data, period=14):
    if len(data) <= period:
        return None
    ups = downs = 0.0
    for a, b in zip(data[-period - 1:-1], data[-period:]):
        up = max(float(b["high"]) - float(a["high"]), 0.0)
        down = max(float(a["low"]) - float(b["low"]), 0.0)
        ups += up; downs += down
    total = ups + downs
    return 0.5 if total == 0 else ups / total


def _psar_direction(data):
    """Compact PSAR-style direction proxy, deliberately confirmation-only."""
    if len(data) < 5:
        return 0
    closes = [float(x["close"]) for x in data[-5:]]
    lows = [float(x["low"]) for x in data[-5:]]
    highs = [float(x["high"]) for x in data[-5:]]
    last = closes[-1]
    up = last > sum(lows) / len(lows) and closes[-1] >= closes[-2] >= closes[-3]
    down = last < sum(highs) / len(highs) and closes[-1] <= closes[-2] <= closes[-3]
    return 1 if up and not down else -1 if down and not up else 0


def _vote(direction, *signals):
    """Return +1 when the reference method agrees with direction, else 0/-1."""
    wanted = 1 if direction == "UP" else -1
    clean = [int(s) for s in signals if int(s) in (-1, 0, 1)]
    if not clean:
        return 0
    score = sum(clean)
    if score * wanted > 0:
        return 1
    if score * wanted < 0:
        return -1
    return 0


def method_votes(data, direction, mtf=None):
    """Evaluate the supplied indicator strategy as a consensus layer.

    The returned values are votes, not standalone trade commands.  The actual
    Candice decision still requires real-candle freshness, market structure,
    and quality gates.
    """
    if len(data) < 30:
        return {"methods": {}, "consensus": 0.0, "active": 0}

    closes = [float(x["close"]) for x in data]
    last = closes[-1]
    prev = closes[-2]
    e5, e9, e21, e50 = (_ema(closes, p) for p in (5, 9, 21, 50))
    votes = {}

    # Moving Average Crossover: fast MA vs slow MA.
    if e9 is not None and e21 is not None:
        votes["moving_average_crossover"] = _vote(direction, 1 if e9 > e21 else -1 if e9 < e21 else 0)

    # DeMarker / Stochastic / RSI: overbought/oversold are reversal signals,
    # not automatic entries. Only score extremes and leave neutral untouched.
    dm = _demarker(data)
    votes["demarker_overbought_oversold"] = _vote(direction, -1 if dm is not None and dm > 0.70 else 1 if dm is not None and dm < 0.30 else 0)

    stoch = _stochastic(data)
    votes["stochastic_overbought_oversold"] = _vote(direction, -1 if stoch is not None and stoch > 80 else 1 if stoch is not None and stoch < 20 else 0)

    macd = _macd(closes)
    votes["macd_crossover"] = _vote(direction, 1 if macd is not None and macd > 0 else -1 if macd is not None and macd < 0 else 0)

    rsi = _rsi(closes)
    votes["rsi_overbought_oversold"] = _vote(direction, -1 if rsi is not None and rsi > 70 else 1 if rsi is not None and rsi < 30 else 0)

    bb = _bollinger(closes)
    if bb is None:
        votes["bollinger_bands_breakout"] = 0
    else:
        mean, upper, lower = bb
        raw = 1 if last > upper and last > prev else -1 if last < lower and last < prev else 0
        votes["bollinger_bands_breakout"] = _vote(direction, raw)

    psar = _psar_direction(data)
    votes["parabolic_sar_reversal"] = _vote(direction, psar)

    roc = _roc(closes)
    votes["rate_of_change_crossover"] = _vote(direction, 1 if roc is not None and roc > 0 else -1 if roc is not None and roc < 0 else 0)

    dc = _donchian(data)
    if dc is None:
        votes["donchian_channel_breakout"] = 0
    else:
        hi, lo = dc
        raw = 1 if last > hi else -1 if last < lo else 0
        votes["donchian_channel_breakout"] = _vote(direction, raw)

    # Existing method families, kept as a separate consensus so the reference
    # strategy does not replace market structure.
    atr = _atr(data) or 0.0
    drift = (last - closes[-6]) if len(closes) >= 6 else 0.0
    trend = 1 if e9 and e21 and e9 > e21 else -1 if e9 and e21 and e9 < e21 else 0
    votes["trend_following"] = _vote(direction, trend)
    votes["breakout"] = votes["donchian_channel_breakout"]
    votes["pullback"] = _vote(direction, 1 if e9 and last > e9 and drift > 0 else -1 if e9 and last < e9 and drift < 0 else 0)
    votes["momentum"] = _vote(direction, 1 if drift > 0 and atr >= 0 else -1 if drift < 0 and atr >= 0 else 0)
    votes["reversal"] = _vote(direction, votes["rsi_overbought_oversold"], votes["stochastic_overbought_oversold"], votes["demarker_overbought_oversold"])
    votes["mean_reversion"] = votes["reversal"]
    votes["market_structure"] = _vote(direction, trend)
    votes["price_action"] = _vote(direction, 1 if last > prev else -1 if last < prev else 0)
    votes["candlestick"] = votes["price_action"]
    votes["support_resistance"] = votes["bollinger_bands_breakout"]
    votes["volatility"] = _vote(direction, 1 if atr > 0 else 0)

    # MTF is a weighted confirmation.  Normalize external signs when supplied.
    mtf = mtf or {}
    mtf_votes = []
    for tf in ("1m", "3m", "5m"):
        try:
            mtf_votes.append(1 if float(mtf.get(tf, 0) or 0) > 0 else -1 if float(mtf.get(tf, 0) or 0) < 0 else 0)
        except Exception:
            mtf_votes.append(0)
    if any(mtf_votes):
        votes["multi_timeframe"] = _vote(direction, *mtf_votes)
    else:
        votes["multi_timeframe"] = 0

    active = [v for v in votes.values() if v]
    consensus = (sum(active) / len(active)) if active else 0.0
    return {"methods": votes, "consensus": round(consensus, 3), "active": len(active)}


if __name__ == "__main__":
    print(json.dumps(train_snapshot(), separators=(",", ":")))

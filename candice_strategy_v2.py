"""Candice Strategy V2: pattern -> next candle expectation -> context -> expiry.

This module is deliberately conservative. A candlestick pattern never creates a
signal by itself; it describes what confirmation candle is required and what
expiry is appropriate if the confirmation arrives.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable

EXPIRIES = (1, 2, 3, 5, 10, 15)


@dataclass(frozen=True)
class PatternRule:
    name: str
    bias: str
    confirmation: str
    base_expiries: tuple[int, ...]
    reversal: bool = False


@dataclass(frozen=True)
class StrategyPlan:
    pattern: str
    pattern_bias: str
    situation: str
    next_candle_timeframe: str
    next_candle_direction: str
    confirmation_required: str
    allowed_expiries: tuple[int, ...]
    recommended_expiry: int
    confidence_adjustment: int
    wait: bool
    reasons: tuple[str, ...]
    risk_flags: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


# Every pattern emitted by candice_patterns.py has an explicit strategy rule.
# A pattern is evidence only; confirmation is still required before a signal.
RULES = {
    "DOJI": PatternRule("DOJI", "NEUTRAL", "Wait for the next closed candle to break the Doji high/low with body confirmation.", (1, 2)),
    "SPINNING TOP": PatternRule("SPINNING TOP", "NEUTRAL", "Require the next candle to close beyond the pattern range in the chosen direction.", (1, 2)),
    "HAMMER": PatternRule("HAMMER", "UP", "Next candle must break/close above the Hammer high, preferably from support.", (1, 2, 3), True),
    "INVERTED HAMMER": PatternRule("INVERTED HAMMER", "UP", "Next candle must close above the Inverted Hammer high after support/reversal context.", (1, 2, 3), True),
    "HANGING MAN": PatternRule("HANGING MAN", "DOWN", "Next candle must break/close below the Hanging Man low at a mature uptrend/resistance.", (1, 2, 3), True),
    "SHOOTING STAR": PatternRule("SHOOTING STAR", "DOWN", "Next candle must break/close below the Shooting Star low near resistance.", (1, 2, 3), True),
    "BULLISH ENGULFING": PatternRule("BULLISH ENGULFING", "UP", "Next candle should hold above the engulfing midpoint/high and continue upward.", (2, 3)),
    "BEARISH ENGULFING": PatternRule("BEARISH ENGULFING", "DOWN", "Next candle should hold below the engulfing midpoint/low and continue downward.", (2, 3)),
    "BULLISH HARAMI": PatternRule("BULLISH HARAMI", "UP", "Next candle must close above the Harami high.", (2, 3)),
    "BEARISH HARAMI": PatternRule("BEARISH HARAMI", "DOWN", "Next candle must close below the Harami low.", (2, 3)),
    "PIERCING LINE": PatternRule("PIERCING LINE", "UP", "Next candle must confirm above the pattern midpoint/high.", (2, 3)),
    "DARK CLOUD COVER": PatternRule("DARK CLOUD COVER", "DOWN", "Next candle must confirm below the pattern midpoint/low.", (2, 3)),
    "TWEEZER BOTTOM": PatternRule("TWEEZER BOTTOM", "UP", "Next candle must break the two-candle high from support.", (1, 2, 3), True),
    "TWEEZER TOP": PatternRule("TWEEZER TOP", "DOWN", "Next candle must break the two-candle low from resistance.", (1, 2, 3), True),
    "MORNING STAR": PatternRule("MORNING STAR", "UP", "Next candle must continue above the star structure with bullish body.", (3, 5), True),
    "EVENING STAR": PatternRule("EVENING STAR", "DOWN", "Next candle must continue below the star structure with bearish body.", (3, 5), True),
    "THREE WHITE SOLDIERS": PatternRule("THREE WHITE SOLDIERS", "UP", "Next candle should preserve higher closes without immediate rejection.", (3, 5)),
    "THREE BLACK CROWS": PatternRule("THREE BLACK CROWS", "DOWN", "Next candle should preserve lower closes without immediate rejection.", (3, 5)),
    "BULLISH KICKER": PatternRule("BULLISH KICKER", "UP", "Next candle should hold the gap/impulse and continue above the kicker high.", (2, 3, 5)),
    "BEARISH KICKER": PatternRule("BEARISH KICKER", "DOWN", "Next candle should hold the impulse and continue below the kicker low.", (2, 3, 5)),
    "BULLISH THREE INSIDE": PatternRule("BULLISH THREE INSIDE", "UP", "Next candle must close above the three-inside high with bullish body.", (2, 3), True),
    "BEARISH THREE INSIDE": PatternRule("BEARISH THREE INSIDE", "DOWN", "Next candle must close below the three-inside low with bearish body.", (2, 3), True),
    "BULLISH THREE OUTSIDE": PatternRule("BULLISH THREE OUTSIDE", "UP", "Next candle must hold above the outside structure high.", (1, 2, 3)),
    "BEARISH THREE OUTSIDE": PatternRule("BEARISH THREE OUTSIDE", "DOWN", "Next candle must hold below the outside structure low.", (1, 2, 3)),
    "ABANDONED BABY BULLISH": PatternRule("ABANDONED BABY BULLISH", "UP", "Next candle must confirm above the abandoned-baby structure high.", (2, 3, 5), True),
    "ABANDONED BABY BEARISH": PatternRule("ABANDONED BABY BEARISH", "DOWN", "Next candle must confirm below the abandoned-baby structure low.", (2, 3, 5), True),
    "INSIDE BAR": PatternRule("INSIDE BAR", "NEUTRAL", "Trade only after a confirmed break of the inside-bar high or low.", (1, 2)),
    "OUTSIDE BAR": PatternRule("OUTSIDE BAR", "NEUTRAL", "Require the next candle to confirm the closing-side breakout; reject immediate reversal conflict.", (1, 2, 3)),
    "BULLISH BREAKOUT CANDLE": PatternRule("BULLISH BREAKOUT CANDLE", "UP", "Next candle must hold above the broken level and avoid immediate rejection.", (1, 2, 3)),
    "BEARISH BREAKDOWN CANDLE": PatternRule("BEARISH BREAKDOWN CANDLE", "DOWN", "Next candle must hold below the broken level and avoid immediate rejection.", (1, 2, 3)),
    "MARUBOZU BULLISH": PatternRule("MARUBOZU BULLISH", "UP", "Next candle should preserve the impulse; avoid chasing directly into resistance.", (1, 2, 3)),
    "MARUBOZU BEARISH": PatternRule("MARUBOZU BEARISH", "DOWN", "Next candle should preserve the impulse; avoid chasing directly into support.", (1, 2, 3)),
}


def _first_pattern(patterns: Iterable[str]) -> str:
    for raw in patterns or ():
        key = str(raw).strip().upper()
        if key in RULES:
            return key
    return ""


def _same(frame: dict, direction: str) -> bool:
    return bool(direction and frame and frame.get("direction") == direction)


def build_plan(snapshot: dict, frames: dict | None = None, patterns: Iterable[str] | None = None) -> StrategyPlan:
    frames = frames or {}
    patterns = tuple(patterns or snapshot.get("candle_patterns", ()) or ())
    pattern = _first_pattern(patterns)
    rule = RULES.get(pattern, PatternRule("NONE", "NEUTRAL", "No pattern-specific confirmation; use independent technical confirmation.", ()))

    one = frames.get("1m", snapshot)
    three = frames.get("3m", {})
    five = frames.get("5m", snapshot)
    ten = frames.get("10m", {})
    fifteen = frames.get("15m", {})

    trend = str(five.get("direction", ""))
    adx = float(five.get("adx14", 0) or 0)
    strength = float(five.get("strength", 0) or 0)
    body = float(one.get("body_ratio", 0) or 0)
    rsi = float(one.get("rsi14", 50) or 50)
    conflicts = int(one.get("indicator_conflicts", 0) or 0)

    up = sum(_same(f, "UP") for f in (one, three, five, ten, fifteen))
    down = sum(_same(f, "DOWN") for f in (one, three, five, ten, fifteen))
    mtf = max(up, down)
    high_tf = _same(ten, trend) and _same(fifteen, trend)

    if conflicts >= 3 or (rsi >= 82 or rsi <= 18):
        situation = "TRANSITION"
    elif adx < 18:
        situation = "RANGE"
    elif strength >= .8 and adx >= 30 and body >= .55:
        situation = "STRONG_TREND"
    elif strength >= .8 and adx >= 22:
        situation = "TREND_CONTINUATION"
    elif pattern and rule.reversal and rule.bias and rule.bias != trend:
        situation = "REVERSAL_SETUP"
    elif pattern and rule.bias == trend and body < .35:
        situation = "PULLBACK_CONFIRMATION"
    elif strength >= .8 and int(snapshot.get("indicator_agreement", 0) or 0) >= 4:
        situation = "BREAKOUT"
    else:
        situation = "NEUTRAL"

    expected = rule.bias
    reasons: list[str] = []
    risks: list[str] = []
    allowed = tuple(rule.base_expiries)
    adjustment = 0

    if expected == "NEUTRAL":
        expected = trend if trend in ("UP", "DOWN") else "WAIT"
        adjustment -= 8
        risks.append("pattern is not directional by itself")

    if rule.reversal and trend and rule.bias != trend:
        reasons.append("reversal pattern is opposite the primary trend")
        adjustment -= 8
        risks.append("counter-trend reversal")

    if trend and rule.bias == trend:
        reasons.append("pattern agrees with 5m direction")
        adjustment += 6

    if mtf >= 4:
        reasons.append("multi-timeframe alignment")
        adjustment += 8
    elif mtf <= 2:
        risks.append("weak multi-timeframe alignment")
        adjustment -= 8

    if adx >= 30:
        reasons.append("strong directional momentum")
    elif adx < 18:
        risks.append("range/low-trend-strength environment")

    if body >= .55:
        reasons.append("strong current candle body")
    elif body < .25:
        risks.append("weak current candle body")
        adjustment -= 5

    if high_tf:
        reasons.append("10m and 15m support the primary direction")

    if situation == "STRONG_TREND" and mtf >= 3:
        preferred = 1 if body >= .75 and adx >= 35 else 2
        allowed = tuple(x for x in (1, 2, 3) if x in EXPIRIES)
    elif situation == "BREAKOUT":
        preferred = 1 if body >= .65 else 2
        allowed = tuple(x for x in (1, 2, 3) if x in EXPIRIES)
    elif situation == "TREND_CONTINUATION":
        preferred = 3 if 3 in allowed else (2 if 2 in allowed else 1)
    elif situation == "PULLBACK_CONFIRMATION":
        preferred = 3 if 3 in allowed else 2
    elif situation == "REVERSAL_SETUP":
        preferred = 2 if 2 in allowed else 1
    elif high_tf and adx >= 25 and strength >= .8:
        preferred = 10
        allowed = (5, 10, 15)
    else:
        preferred = allowed[0] if allowed else 0

    if preferred >= 10 and not (high_tf and adx >= 25 and strength >= .8):
        preferred = 5 if 5 in allowed else 3 if 3 in allowed else 0
        risks.append("higher expiry blocked without higher-timeframe support")

    wait = False
    if not pattern:
        risks.append("no recognized pattern")
    if conflicts >= 3:
        wait = True
        reasons.append("indicator conflict requires WAIT")
    if rsi >= 82 or rsi <= 18:
        wait = True
        reasons.append("extreme RSI blocks forced entry")
    if expected == "WAIT" or not allowed or preferred == 0:
        wait = True
    if rule.reversal and rule.bias != trend and mtf < 4:
        wait = True
        reasons.append("reversal lacks enough multi-timeframe confirmation")

    return StrategyPlan(
        pattern=pattern or "NONE",
        pattern_bias=rule.bias,
        situation=situation,
        next_candle_timeframe="1m closed candle",
        next_candle_direction=expected,
        confirmation_required=rule.confirmation,
        allowed_expiries=allowed,
        recommended_expiry=0 if wait else preferred,
        confidence_adjustment=adjustment,
        wait=wait,
        reasons=tuple(dict.fromkeys(reasons)),
        risk_flags=tuple(dict.fromkeys(risks)),
    )

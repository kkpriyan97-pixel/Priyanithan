"""Candice Own Strategy Brain V4.

Adds adaptive, outcome-aware reasoning on top of Strategy V2. It does not invent
certainty and it never places trades. With insufficient samples it stays neutral;
with enough samples it can veto weak historical contexts and shorten riskier
expiry choices.
"""
from __future__ import annotations

import time
from dataclasses import replace

from candice_memory import learning_context, record
from candice_strategy_v2 import build_plan as build_v2_plan

MIN_SAMPLES = 12
MIN_EDGE = 0.08


def _rate(bucket):
    if not isinstance(bucket, dict):
        return None
    n = int(bucket.get("samples", 0) or 0)
    if n < MIN_SAMPLES:
        return None
    return float(bucket.get("win_rate", 0) or 0) / 100.0


def _pattern_rate(ctx, patterns):
    values = []
    table = ctx.get("by_pattern", {}) or {}
    for p in patterns or ():
        r = _rate(table.get(str(p)))
        if r is not None:
            values.append(r)
    return sum(values) / len(values) if values else None


def _probability(snapshot, plan, ctx):
    direction = str(plan.next_candle_direction or "").upper()
    expiry = plan.recommended_expiry
    regime = "TREND" if float(snapshot.get("adx14", 0) or 0) >= 25 and float(snapshot.get("strength", 0) or 0) >= .8 else "RANGE" if float(snapshot.get("adx14", 0) or 0) < 18 else "TRANSITION"
    values = []
    r = _rate((ctx.get("by_direction", {}) or {}).get(direction))
    if r is not None: values.append(r)
    r = _rate((ctx.get("by_regime", {}) or {}).get(regime))
    if r is not None: values.append(r)
    r = _rate((ctx.get("by_expiry", {}) or {}).get(str(expiry)))
    if r is not None: values.append(r)
    r = _pattern_rate(ctx, plan.pattern and (plan.pattern,) or ())
    if r is not None: values.append(r)
    if not values:
        return .50, regime, 0
    prior = sum(values) / len(values)
    samples = int((ctx.get("overall", {}) or {}).get("samples", 0) or 0)
    weight = min(.65, samples / 200.0)
    return .50 * (1.0 - weight) + prior * weight, regime, samples


def build_plan(snapshot: dict, frames: dict | None = None, patterns=None):
    base = build_v2_plan(snapshot, frames, patterns)
    ctx = learning_context(None)
    probability, regime, samples = _probability(snapshot, base, ctx)
    reasons = list(base.reasons)
    risks = list(base.risk_flags)
    adjustment = int(base.confidence_adjustment)

    # Fresh-session safety: do not let a new strategy override the proven V2
    # logic until enough outcome samples exist.
    if samples < MIN_SAMPLES:
        reasons.append("own brain cold-start: V2 remains authoritative")
        adjustment = min(adjustment, 0)
        final = base
    else:
        if probability >= .60:
            reasons.append(f"own historical probability {probability:.0%}")
            adjustment += 5
        elif probability < .58:
            risks.append(f"own historical probability only {probability:.0%}")
            adjustment -= 8

        veto = probability < (0.50 + MIN_EDGE)
        if veto:
            risks.append("own strategy historical edge below safety threshold")
            final = replace(base, wait=True, recommended_expiry=0,
                            confidence_adjustment=adjustment,
                            reasons=tuple(dict.fromkeys(reasons + ["OWN BRAIN → WAIT"])),
                            risk_flags=tuple(dict.fromkeys(risks)))
        else:
            # The own brain may shorten an expiry but never lengthen a weak setup.
            expiry = base.recommended_expiry
            if probability < .66 and expiry >= 5:
                expiry = 3 if 3 in base.allowed_expiries else 2 if 2 in base.allowed_expiries else expiry
                risks.append("own brain shortened expiry because edge is moderate")
            final = replace(base, recommended_expiry=expiry,
                            confidence_adjustment=adjustment,
                            reasons=tuple(dict.fromkeys(reasons)),
                            risk_flags=tuple(dict.fromkeys(risks)))

    record({
        "ts": time.time(),
        "type": "own_strategy_session",
        "direction": final.next_candle_direction,
        "pattern": final.pattern,
        "situation": final.situation,
        "regime": regime,
        "expiry": final.recommended_expiry,
        "probability": round(probability, 4),
        "samples": samples,
        "wait": final.wait,
    })
    return final

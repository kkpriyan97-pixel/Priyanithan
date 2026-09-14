"""Candice signal-quality fix: allow strong technical continuation without a named candle pattern.

DEMO/MANUAL ONLY. This does not place trades and does not bypass conflict/extreme-RSI safety.
"""
from __future__ import annotations

from dataclasses import replace


def install(app):
    if getattr(app, "_candice_signal_quality_fix_v1", False):
        return

    import candice_engine as engine
    import candice_strategy_v2 as v2

    # Patch V2 before V4 imports its build_v2_plan alias. A named candlestick
    # pattern is useful evidence, but should not be a mandatory prerequisite
    # when independent technical confirmation is already strong.
    if not getattr(v2, "_candice_patternless_fix_v1", False):
        original_v2 = v2.build_plan

        def improved_build_plan(snapshot, frames=None, patterns=None):
            plan = original_v2(snapshot, frames, patterns)
            pattern = str(plan.pattern or "").upper()
            if pattern not in ("", "NONE") or not plan.wait:
                return plan

            frames = frames or {}
            one = frames.get("1m", snapshot) or {}
            three = frames.get("3m", {}) or {}
            five = frames.get("5m", snapshot) or {}
            ten = frames.get("10m", {}) or {}
            fifteen = frames.get("15m", {}) or {}
            direction = str(five.get("direction") or one.get("direction") or "").upper()
            if direction not in ("UP", "DOWN"):
                return plan

            strength = float(five.get("strength", 0) or 0)
            adx = float(five.get("adx14", 0) or 0)
            agreement = int(five.get("indicator_agreement", 0) or 0)
            conflicts = int(five.get("indicator_conflicts", 0) or 0)
            body = float(one.get("body_ratio", 0) or 0)
            rsi = float(one.get("rsi14", 50) or 50)
            aligned = sum(1 for f in (one, three, five, ten, fifteen) if f and f.get("direction") == direction)
            high_tf = bool(ten.get("direction") == direction and fifteen.get("direction") == direction)

            # Strong independent continuation/breakout qualification.
            strong = (
                strength >= 0.80 and adx >= 22 and agreement >= 4 and
                conflicts <= 1 and body >= 0.45 and aligned >= 3 and
                not (rsi >= 82 or rsi <= 18)
            )
            if not strong:
                return plan

            if adx >= 35 and body >= 0.55:
                expiry = 1
            elif adx >= 30:
                expiry = 2
            else:
                expiry = 3
            if high_tf and adx >= 25 and strength >= 0.80 and body < 0.75:
                expiry = 3

            reasons = list(plan.reasons)
            reasons.append("patternless technical continuation confirmed")
            reasons.append(f"independent confirmation: MTF {aligned}/5, indicators {agreement}/5, ADX {adx:.1f}")
            risks = [r for r in plan.risk_flags if "no recognized pattern" not in str(r).lower()]
            return replace(
                plan,
                situation="BREAKOUT" if plan.situation in ("NEUTRAL", "BREAKOUT") else plan.situation,
                next_candle_direction=direction,
                confirmation_required="Closed-candle technical confirmation; named candlestick pattern not required when independent confirmation is strong.",
                allowed_expiries=(1, 2, 3),
                recommended_expiry=expiry,
                confidence_adjustment=plan.confidence_adjustment + 4,
                wait=False,
                reasons=tuple(dict.fromkeys(reasons)),
                risk_flags=tuple(dict.fromkeys(risks)),
            )

        v2.build_plan = improved_build_plan
        v2._candice_patternless_fix_v1 = True
        app.log.info("CANDICE SIGNAL QUALITY V1 — strong technical continuation allowed without named candlestick pattern")

    # Make every AI provider understand that a textbook candle name is not a
    # hard requirement. The existing hard gates still decide final safety.
    original_prompt = engine._ai_prompt
    if not getattr(engine, "_candice_ai_policy_fix_v1", False):
        def improved_prompt(snapshot, memory):
            base = original_prompt(snapshot, memory)
            return base + "\nPOLICY OVERRIDE: Do NOT reject solely because there is no named candlestick pattern. A strong closed-candle breakout/continuation may be approved when independent indicators, MTF alignment, momentum, body strength and risk checks agree. A named candle is evidence, not a mandatory gate. Still reject conflicts, extreme RSI, weak structure, stale data, unsafe expiry, or low confidence."
        engine._ai_prompt = improved_prompt
        engine._candice_ai_policy_fix_v1 = True
        app.log.info("CANDICE AI POLICY V1 — named candlestick pattern is optional when independent confirmation is strong")

    app._candice_signal_quality_fix_v1 = True

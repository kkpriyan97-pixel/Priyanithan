from __future__ import annotations


def install(app):
    """Balance V4/V2 conservatism: HTF disagreement is context, not an automatic veto."""
    if getattr(app, '_candice_strategy_balancer_v1', False):
        return
    import candice_engine as engine

    base_brain = engine.human_brain

    def balanced_brain(frames, memory=None):
        result = dict(base_brain(frames, memory) or {})
        s = frames.get('5m') or frames.get('1m') or {}
        one = frames.get('1m', {})
        three = frames.get('3m', {})
        direction = str(result.get('direction') or s.get('direction') or '').upper()
        pattern_dir = str(s.get('pattern_direction') or '').upper()
        strength = float(s.get('strength', 0) or 0)
        agreement = int(s.get('indicator_agreement', 0) or 0)
        conflicts = sum(1 for k in ('10m', '15m') if frames.get(k, {}).get('direction') and frames.get(k, {}).get('direction') != direction)
        core_aligned = sum(1 for f in (one, three, s) if f.get('direction') == direction)
        strong_core = core_aligned >= 2 and (strength >= 0.8 or agreement >= 3)
        pattern_support = bool(direction and pattern_dir == direction)

        # The base brain's >=3 total-TF conflict veto is too strict when the
        # short-term structure is coherent. Keep a hard veto for weak core data,
        # but allow a qualified candidate when 1m/3m/5m support the same direction.
        if result.get('approve') is False and direction in {'UP', 'DOWN'} and conflicts >= 2 and strong_core and (pattern_support or agreement >= 4):
            score = float(result.get('score', 0) or 0)
            if score >= 0.60 and float(s.get('adx14', 0) or 0) >= 18 and not (float(s.get('rsi14', 50) or 50) >= 82 or float(s.get('rsi14', 50) or 50) <= 18):
                result['approve'] = True
                result['expiry'] = 3 if float(s.get('adx14', 0) or 0) >= 22 else 5
                result['reason'] = str(result.get('reason') or '') + '; HTF disagreement treated as context risk; core 1m/3m/5m confirmation retained'
                result['strategy_balanced'] = True
        return result

    engine.human_brain = balanced_brain

    base_prompt = engine._ai_prompt
    def balanced_prompt(s, memory):
        prompt = base_prompt(s, memory)
        prompt = prompt.replace(
            'Approve only when fresh price action, trend, momentum, volatility, multi-timeframe context and candle structure agree. Reject weak, stale, contradictory, overextended or reversal-risk setups.',
            'Approve when fresh price action, trend, momentum, volatility and candle structure form a coherent core setup. Treat 1m/3m/5m agreement as the primary execution context; 10m/15m are higher-level context and should reduce confidence when they disagree, but must not automatically veto a strong short-term setup. Reject weak, stale, internally contradictory, overextended or high-risk setups.'
        )
        prompt = prompt.replace(
            'Shorter duration is for strong immediate momentum; 10/15 only with higher-timeframe support.',
            'Shorter duration is for strong immediate momentum and may be used when 1m/3m/5m agree; 10/15 require higher-timeframe support.'
        )
        return prompt

    engine._ai_prompt = balanced_prompt
    app._candice_strategy_balancer_v1 = True
    app.log.info('CANDICE STRATEGY BALANCER V1 ACTIVE — 1M/3M/5M CORE + HTF CONTEXT PENALTY, NO FORCED SIGNAL')

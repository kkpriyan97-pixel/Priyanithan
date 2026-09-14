from __future__ import annotations


def install(app):
    """Make every V4/V2 strategy decision visible without changing trade safety."""
    if getattr(app, '_candice_strategy_audit_v1', False):
        return
    import candice_engine as engine
    base_ai = engine.ai_review

    def audited_ai(ai_input, memory=None):
        result = dict(base_ai(ai_input, memory) or {})
        plan = ai_input.get('strategy_v2') or {}
        pattern = str(plan.get('pattern') or 'NONE')
        situation = str(plan.get('situation') or 'UNKNOWN')
        bias = str(plan.get('pattern_bias') or 'NEUTRAL')
        next_dir = str(plan.get('next_candle_direction') or 'WAIT')
        confirmation = str(plan.get('confirmation_required') or 'Independent confirmation required')
        expiry = plan.get('recommended_expiry') or 0
        allowed = plan.get('allowed_expiries') or ()
        wait = bool(plan.get('wait'))
        reasons = tuple(plan.get('reasons') or ())
        risks = tuple(plan.get('risk_flags') or ())
        version = str(ai_input.get('human_brain_evidence', {}).get('strategy_version') or 'V4')
        audit = (
            f'STRATEGY {version} APPLIED | pattern={pattern} | bias={bias} | '
            f'situation={situation} | next={next_dir} | confirmation={confirmation} | '
            f'allowed_expiry={list(allowed)} | recommended_expiry={expiry} | wait={wait}'
        )
        if reasons:
            audit += ' | reasons=' + '; '.join(str(x) for x in reasons)
        if risks:
            audit += ' | risks=' + '; '.join(str(x) for x in risks)
        result['reason'] = audit + ' || AI: ' + str(result.get('reason') or 'no AI reason')
        result['_candice_strategy_audit'] = audit
        engine.log.info('CANDICE STRATEGY AUDIT %s', audit)
        return result

    engine.ai_review = audited_ai
    app._candice_strategy_audit_v1 = True
    app.log.info('CANDICE STRATEGY AUDIT V1 ACTIVE — EVERY V2/V4 PATTERN DECISION EXPOSED TO FINAL GATE')

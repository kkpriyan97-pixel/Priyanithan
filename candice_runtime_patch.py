from __future__ import annotations
import asyncio
import time
from datetime import datetime


def install(app):
    """Runtime layer: 1m research, 5m signal slots, AI as the main decision manager."""
    if getattr(app, '_candice_runtime_patch_v5', False):
        return
    import candice_engine as engine
    import candice_memory as memory
    from candice_strategy_v2 import build_plan

    engine.AI_TIMEOUT = min(float(getattr(engine, 'AI_TIMEOUT', 8.0)), 8.0)

    base_summary = memory.summary
    def learned_summary(asset=None):
        data = base_summary(asset)
        by_expiry = dict(data.get('by_expiry', {})); by_direction = dict(data.get('by_direction', {}))
        if not by_expiry or not by_direction:
            for e in memory.recent(asset, 100):
                if e.get('result') not in ('WIN', 'LOSS'): continue
                ek = str(e.get('expiry', '?')); dk = str(e.get('direction', '?'))
                by_expiry.setdefault(ek, [0, 0]); by_direction.setdefault(dk, [0, 0])
                by_expiry[ek][0] += int(e.get('result') == 'WIN'); by_expiry[ek][1] += int(e.get('result') == 'LOSS')
                by_direction[dk][0] += int(e.get('result') == 'WIN'); by_direction[dk][1] += int(e.get('result') == 'LOSS')
        data['by_expiry'] = by_expiry; data['by_direction'] = by_direction; data['learning_mode'] = 'outcome-calibrated'
        data['recovery_mode'] = any(selected_asset == asset and app.recovery_until.get(uid, 0) > time.time() for uid, selected_asset in list(app.selected.items()))
        return data
    app.summary = learned_summary

    base_brain = engine.human_brain
    def learned_brain(frames, memory_context=None):
        ctx = memory_context or {}; result = base_brain(frames, ctx)
        try:
            primary = frames.get('5m') or frames.get('1m') or {}
            plan = build_plan(primary, frames, primary.get('candle_patterns', ()))
            result = dict(result)
            result['strategy_v2'] = plan.to_dict()
            result['next_candle_direction'] = plan.next_candle_direction
            result['next_candle_timeframe'] = plan.next_candle_timeframe
            result['confirmation_required'] = plan.confirmation_required
            result['recommended_expiry_v2'] = plan.recommended_expiry
            result['allowed_expiries_v2'] = plan.allowed_expiries
            result['situation_v2'] = plan.situation
            result['risk_flags_v2'] = plan.risk_flags
            result['strategy_reasons_v2'] = plan.reasons
            if plan.wait:
                result['approve'] = False
                result['expiry'] = 0
                result['reason'] = 'Strategy V2 WAIT: ' + ('; '.join(plan.reasons + plan.risk_flags) or 'insufficient confirmation')
            engine.log.info('CANDICE STRATEGY V2 pair=%s pattern=%s situation=%s next=%s/%s expiry=%s wait=%s',ctx.get('asset',''),plan.pattern,plan.situation,plan.next_candle_direction,plan.next_candle_timeframe,plan.recommended_expiry,plan.wait)
        except Exception as exc:
            engine.log.warning('CANDICE STRATEGY V2 enrichment failed: %s', exc)
        if not ctx.get('recovery_mode'): return result
        direction = result.get('direction', '')
        aligned = sum(1 for key in ('1m','3m','5m','10m','15m') if frames.get(key, {}).get('direction') == direction)
        agreement = int((frames.get('5m') or frames.get('1m') or {}).get('indicator_agreement', 0) or 0)
        score = float(result.get('score', 0) or 0)
        strict_ok = bool(result.get('expiry') in engine.EXPIRIES and score >= 0.78 and aligned >= 4 and agreement >= 4 and result.get('regime') in {'trend','breakout'})
        result['approve'] = bool(result.get('approve') and strict_ok); result['recovery_mode'] = True; result['reason'] = 'RECOVERY STRICT: ' + str(result.get('reason', ''))
        engine.log.info('RECOVERY GATE %s pair=%s score=%.2f aligned=%s agreement=%s', 'APPROVE' if strict_ok else 'REJECT', ctx.get('asset',''), score, aligned, agreement)
        return result
    engine.human_brain = learned_brain

    async def orchestrator_analyze(asset, broker_obj, memory_context):
        def finish(result):
            app._candice_last_analysis[asset] = result
            engine.log.info('CANDICE AI DECISION RESULT pair=%s decision=%s direction=%s confidence=%s expiry=%s reason=%s', asset, getattr(result,'decision',''), getattr(result,'direction',''), getattr(result,'confidence',0), getattr(result,'expiry',0), getattr(result,'reason',''))
            return result

        df, err = await broker_obj.candles(asset, 60, engine.ANALYSIS_CANDLE_COUNT, 360)
        if err or df is None or len(df) < 120: return finish(engine.Analysis(asset, 'REJECT', reason=err or 'insufficient fresh candles'))
        base = engine.closed_1m(df)
        if len(base) < 120: return finish(engine.Analysis(asset, 'REJECT', reason=f'insufficient closed candles ({len(base)})'))
        try:
            snap1 = engine.technical_snapshot(base); frames = {'1m': snap1}
            for mins in (3,5,10,15):
                agg = engine.resample_ohlc(base, mins)
                if len(agg) >= 60: frames[f'{mins}m'] = engine.technical_snapshot(agg)
        except Exception as exc:
            return finish(engine.Analysis(asset, 'REJECT', reason=f'technical calculation failed: {exc}'))

        primary = frames.get('5m', snap1)
        brain = engine.human_brain(frames, {**(memory_context or {}), 'asset': asset})
        strategy_v2 = brain.get('strategy_v2', {})
        context_agreement = sum(1 for k in ('10m','15m') if k in frames and frames[k].get('direction') == primary.get('direction'))
        context_conflict = sum(1 for k in ('10m','15m') if k in frames and frames[k].get('direction') and frames[k].get('direction') != primary.get('direction'))
        technical_expiry = engine.choose_expiry(primary, frames)
        v2_expiry = int(brain.get('recommended_expiry_v2', 0) or 0)
        if v2_expiry in engine.EXPIRIES and not brain.get('strategy_v2', {}).get('wait', False): technical_expiry = v2_expiry
        elif brain.get('strategy_v2', {}).get('wait', False): technical_expiry = 0
        if brain.get('expiry') in engine.EXPIRIES: technical_expiry = brain['expiry'] if not brain.get('strategy_v2', {}).get('wait', False) else 0

        ai_input = {'manager_mode':'CANDICE_AI_ORCHESTRATOR','broker':'Olymptrade market-data feed','olymptrade_ai':'not directly exposed as a public API; do not impersonate or scrape it','decision_role':'main human-like market manager','1m':snap1,'3m':frames.get('3m',{}),'5m':primary,'10m':frames.get('10m',{}),'15m':frames.get('15m',{}),'human_brain_evidence':brain,'strategy_v2':strategy_v2,'next_candle_direction':brain.get('next_candle_direction','WAIT'),'next_candle_timeframe':brain.get('next_candle_timeframe','1m closed candle'),'confirmation_required':brain.get('confirmation_required',''),'situation':brain.get('situation_v2',''),'candidate_expiry':technical_expiry,'hard_rules':{'manual_only':True,'auto_trade':False,'allowed_expiry':list(engine.EXPIRIES),'closed_candle_only':True}}
        ai = await asyncio.to_thread(engine.ai_review, ai_input, memory_context or {})
        decision = str(ai.get('decision','')).upper(); ai_direction = str(ai.get('direction','')).upper()
        raw_conf = float(ai.get('confidence',0) or 0); conf = int(round(raw_conf*100)) if 0 <= raw_conf <= 1 else int(round(raw_conf)); conf=max(0,min(100,conf))
        ai_exp = int(ai.get('expiry',0) or 0)
        recovery = bool((memory_context or {}).get('recovery_mode'))
        aligned = sum(1 for key in ('1m','3m','5m','10m','15m') if frames.get(key,{}).get('direction') == ai_direction)
        agreement = int(primary.get('indicator_agreement',0) or 0)

        if decision != 'APPROVE': return finish(engine.Analysis(asset,'REJECT',direction=ai_direction,confidence=conf,expiry=ai_exp,score=float(brain.get('score',0)),reason=str(ai.get('reason','AI manager WAIT')),timeframe='1m+3m+5m+10m+15m',evidence=tuple(brain.get('patterns',()))))
        if brain.get('strategy_v2', {}).get('wait'): return finish(engine.Analysis(asset,'REJECT',direction=ai_direction,confidence=conf,expiry=ai_exp,score=float(brain.get('score',0)),reason='Strategy V2 WAIT — next-candle/context confirmation not safe',timeframe='1m+3m+5m+10m+15m',evidence=tuple(brain.get('patterns',()))))
        if ai_direction not in {'UP','DOWN'}: return finish(engine.Analysis(asset,'REJECT',confidence=conf,reason='AI manager returned invalid direction'))
        if conf < engine.MIN_CONF: return finish(engine.Analysis(asset,'REJECT',direction=ai_direction,confidence=conf,expiry=ai_exp,reason='AI manager confidence below safety threshold'))
        if not snap1.get('direction') or not primary.get('direction'): return finish(engine.Analysis(asset,'REJECT',direction=ai_direction,confidence=conf,expiry=ai_exp,reason='No dominant market direction'))
        if ai_direction not in {snap1.get('direction'), primary.get('direction')}: return finish(engine.Analysis(asset,'REJECT',direction=ai_direction,confidence=conf,expiry=ai_exp,reason='AI direction has no 1m/5m market support'))
        if context_conflict >= 2 and primary.get('strength',0) < 1.0: return finish(engine.Analysis(asset,'REJECT',direction=ai_direction,confidence=conf,expiry=ai_exp,reason='Higher-timeframe context conflicts'))
        if recovery and not (conf >= 78 and aligned >= 4 and agreement >= 4 and brain.get('regime') in {'trend','breakout'}): return finish(engine.Analysis(asset,'REJECT',direction=ai_direction,confidence=conf,expiry=ai_exp,reason='RECOVERY STRICT manager gate'))

        final_expiry = ai_exp
        expiry_source = 'AI'
        if final_expiry not in engine.EXPIRIES:
            technical_direction = str(brain.get('direction') or primary.get('direction') or '').upper()
            if technical_expiry in engine.EXPIRIES and technical_direction == ai_direction:
                final_expiry = int(technical_expiry); expiry_source = 'TECHNICAL_REPAIR'
                engine.log.info('CANDICE EXPIRY REPAIR pair=%s ai_expiry=%s repaired_expiry=%s direction=%s confidence=%s', asset, ai_exp, final_expiry, ai_direction, conf)
            else:
                return finish(engine.Analysis(asset,'REJECT',direction=ai_direction,confidence=conf,expiry=ai_exp,reason='No safe expiry from AI or technical selector'))

        indicator_evidence = tuple(name for name,value in (('Parabolic SAR Reversal',primary.get('psar_direction')),('Moving Average Crossover',primary.get('ma_crossover')),('Donchian Channel Breakout',primary.get('donchian_breakout')),('MACD Crossover',primary.get('macd_crossover')),('Rate of Change Crossover',primary.get('roc_direction'))) if value == ai_direction)
        candle_evidence = tuple(primary.get('bullish_patterns',()) if ai_direction == 'UP' else primary.get('bearish_patterns',()))
        evidence = tuple(dict.fromkeys(indicator_evidence + candle_evidence))
        engine.log.info('CANDICE AI ORCHESTRATOR pair=%s decision=%s direction=%s confidence=%s expiry=%s expiry_source=%s regime=%s v2_situation=%s next=%s/%s pattern=%s brain_score=%.2f aligned=%s recovery=%s',asset,decision,ai_direction,conf,final_expiry,expiry_source,brain.get('regime',''),brain.get('situation_v2',''),brain.get('next_candle_direction',''),brain.get('next_candle_timeframe',''),brain.get('strategy_v2',{}).get('pattern',''),float(brain.get('score',0)),aligned,recovery)
        return finish(engine.Analysis(asset,'APPROVE',direction=ai_direction,confidence=conf,expiry=final_expiry,score=float(brain.get('score',0)),reason=str(ai.get('reason','AI manager approved after multi-source analysis')),timeframe='1m+3m+5m+10m+15m',evidence=evidence))

    app._candice_last_analysis = {}; app.analyze = orchestrator_analyze
    app.log.info('CANDICE AI ORCHESTRATOR ACTIVE — EXPIRY REPAIR V5 — HARD SAFETY RAILS ON')
    original_scan = app.scan_once; app._candice_original_scan = original_scan

    async def patched_scan_once():
        now_ts=time.time(); minute=int(now_ts//60); slot=(minute%5)==4
        app.log.info('CANDICE SIGNAL CADENCE slot=%s cadence=5m next_boundary=%s',slot,datetime.fromtimestamp((minute+1)*60,app.UAE).strftime('%H:%M:%S UAE'))
        if slot:
            await original_scan(); return
        try:
            assets=await app.broker.live_assets()
            for uid,asset in list(app.selected.items()):
                if uid in app.active or asset not in assets: continue
                df,err=await app.broker.candles(asset,60,60,360)
                if err is None and df is not None and not df.empty:
                    snap=app.technical_snapshot(df); app.record({'ts':time.time(),'asset':asset,'research':True,'cadence':'1m-between-5m-signals','direction':snap['direction'],'strength':snap['strength'],'rsi':snap['rsi14'],'adx':snap['adx14']})
                    app.log.info('CANDICE RESEARCH 1M pair=%s direction=%s strength=%.2f RSI=%.1f ADX=%.1f',asset,snap['direction'],snap['strength'],snap['rsi14'],snap['adx14'])
                else: app.log.info('CANDICE RESEARCH 1M REJECT pair=%s reason=%s',asset,err or 'no fresh candles')
        except Exception: app.log.exception('CANDICE 1M research-only cycle failed')
    app.scan_once=patched_scan_once; app.SIGNAL_CADENCE_MINUTES=5; app.SIGNAL_WINDOW_SECOND=50; app._candice_runtime_patch_v5=True
    app.log.info('CANDICE RUNTIME PATCH V5 ACTIVE — 1M AI — 5M SIGNAL CHECKPOINT — SAFE EXPIRY REPAIR — OUTCOME LEARNING — STRATEGY V2')

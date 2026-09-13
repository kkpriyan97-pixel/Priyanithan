from __future__ import annotations
import asyncio
import time
from datetime import datetime


def install(app):
    """Runtime compatibility layer for AI learning/cadence without broker access."""
    if getattr(app, '_candice_runtime_patch_v2', False):
        return
    import candice_engine as engine
    import candice_memory as memory
    engine.AI_TIMEOUT = min(float(getattr(engine, 'AI_TIMEOUT', 8.0)), 8.0)
    base_summary = memory.summary
    def learned_summary(asset=None):
        data = base_summary(asset)
        by_expiry = dict(data.get('by_expiry', {}))
        by_direction = dict(data.get('by_direction', {}))
        if not by_expiry or not by_direction:
            for e in memory.recent(asset, 100):
                if e.get('result') not in ('WIN', 'LOSS'):
                    continue
                ek = str(e.get('expiry', '?')); dk = str(e.get('direction', '?'))
                by_expiry.setdefault(ek, [0, 0]); by_direction.setdefault(dk, [0, 0])
                by_expiry[ek][0] += int(e.get('result') == 'WIN'); by_expiry[ek][1] += int(e.get('result') == 'LOSS')
                by_direction[dk][0] += int(e.get('result') == 'WIN'); by_direction[dk][1] += int(e.get('result') == 'LOSS')
        data['by_expiry'] = by_expiry
        data['by_direction'] = by_direction
        data['learning_mode'] = 'outcome-calibrated'
        recovery_mode = False
        for uid, selected_asset in list(app.selected.items()):
            if selected_asset == asset and app.recovery_until.get(uid, 0) > time.time():
                recovery_mode = True
                break
        data['recovery_mode'] = recovery_mode
        return data
    app.summary = learned_summary
    base_brain = engine.human_brain
    def learned_brain(frames, memory_context=None):
        ctx = memory_context or {}
        result = base_brain(frames, ctx)
        if not ctx.get('recovery_mode'):
            return result
        direction = result.get('direction', '')
        aligned = sum(1 for key in ('1m','3m','5m','10m','15m') if frames.get(key, {}).get('direction') == direction)
        agreement = int((frames.get('5m') or frames.get('1m') or {}).get('indicator_agreement', 0) or 0)
        score = float(result.get('score', 0) or 0)
        strict_ok = bool(result.get('expiry') in engine.EXPIRIES and score >= 0.78 and aligned >= 4 and agreement >= 4 and result.get('regime') in {'trend','breakout'})
        result['approve'] = bool(result.get('approve') and strict_ok)
        result['recovery_mode'] = True
        result['reason'] = 'RECOVERY STRICT: ' + str(result.get('reason', ''))
        if not strict_ok:
            engine.log.info('RECOVERY GATE REJECT pair=%s score=%.2f aligned=%s agreement=%s', ctx.get('asset',''), score, aligned, agreement)
        else:
            engine.log.info('RECOVERY GATE APPROVE pair=%s score=%.2f aligned=%s agreement=%s', ctx.get('asset',''), score, aligned, agreement)
        return result
    engine.human_brain = learned_brain
    original_scan = app.scan_once
    async def patched_scan_once():
        now_ts = time.time(); minute = int(now_ts // 60)
        slot = (minute % 5) == 4
        app.log.info('CANDICE SIGNAL CADENCE slot=%s cadence=5m next_boundary=%s', slot, datetime.fromtimestamp((minute + 1) * 60, app.UAE).strftime('%H:%M:%S UAE'))
        if slot:
            await original_scan()
            return
        try:
            assets = await app.broker.live_assets()
            for uid, asset in list(app.selected.items()):
                if uid in app.active or asset not in assets:
                    continue
                df, err = await app.broker.candles(asset, 60, 60, 360)
                if err is None and df is not None and not df.empty:
                    snap = app.technical_snapshot(df)
                    app.record({'ts': time.time(), 'asset': asset, 'research': True, 'cadence': '1m-between-5m-signals', 'direction': snap['direction'], 'strength': snap['strength'], 'rsi': snap['rsi14'], 'adx': snap['adx14']})
                    app.log.info('CANDICE RESEARCH 1M pair=%s direction=%s strength=%.2f RSI=%.1f ADX=%.1f', asset, snap['direction'], snap['strength'], snap['rsi14'], snap['adx14'])
                else:
                    app.log.info('CANDICE RESEARCH 1M REJECT pair=%s reason=%s', asset, err or 'no fresh candles')
        except Exception:
            app.log.exception('CANDICE 1M research-only cycle failed')
    app.scan_once = patched_scan_once
    app.SIGNAL_CADENCE_MINUTES = 5
    app.SIGNAL_WINDOW_SECOND = 50
    app._candice_runtime_patch_v2 = True
    app.log.info('CANDICE RUNTIME PATCH ACTIVE — 1M RESEARCH — 5M SIGNAL SLOTS — STRICT RECOVERY — OUTCOME LEARNING')

from __future__ import annotations

import time


def install(app):
    """Run Own Brain V4 on every newly closed 1m candle, 24/7.

    This is observation/learning only. It never calls the AI reviewer and never
    places a trade. The existing 5m checkpoint remains the only signal gate.
    """
    if getattr(app, '_candice_own_brain_247', False):
        return

    import candice_engine as engine
    from candice_strategy_v4 import build_plan

    original_scan = app.scan_once
    app._candice_brain_247_original_scan = original_scan
    app._candice_brain_247_last_minute = {}

    async def brain_watch():
        try:
            assets = await app.broker.live_assets()
            for uid, asset in list(app.selected.items()):
                if uid in app.active or asset not in assets:
                    continue
                df, err = await app.broker.candles(asset, 60, engine.ANALYSIS_CANDLE_COUNT, 360)
                if err or df is None or len(df) < 120:
                    app.log.info('CANDICE OWN BRAIN 24/7 WAIT pair=%s reason=%s', asset, err or 'insufficient candles')
                    continue

                closed = engine.closed_1m(df)
                if len(closed) < 120:
                    continue
                candle_minute = int(closed.index[-1].timestamp() // 60)
                if app._candice_brain_247_last_minute.get(asset) == candle_minute:
                    continue
                app._candice_brain_247_last_minute[asset] = candle_minute

                frames = {'1m': engine.technical_snapshot(closed)}
                for mins in (3, 5, 10, 15):
                    agg = engine.resample_ohlc(closed, mins)
                    if len(agg) >= 60:
                        frames[f'{mins}m'] = engine.technical_snapshot(agg)

                primary = frames.get('5m', frames['1m'])
                plan = build_plan(primary, frames, primary.get('candle_patterns', ()))
                app.log.info(
                    'CANDICE OWN BRAIN 24/7 CANDLE pair=%s candle=%s direction=%s pattern=%s situation=%s next=%s/%s probability_gate=%s expiry=%s wait=%s',
                    asset,
                    closed.index[-1].strftime('%H:%M:%S UAE'),
                    frames['1m'].get('direction', ''),
                    plan.pattern,
                    plan.situation,
                    plan.next_candle_direction,
                    plan.next_candle_timeframe,
                    'adaptive',
                    plan.recommended_expiry,
                    plan.wait,
                )
        except Exception:
            app.log.exception('CANDICE OWN BRAIN 24/7 WATCH FAILED')

    async def patched_scan_once():
        await brain_watch()
        await original_scan()

    app.scan_once = patched_scan_once
    app._candice_own_brain_247 = True
    app.log.info('CANDICE OWN BRAIN 24/7 ACTIVE — EVERY CLOSED 1M CANDLE — SIGNALS STILL 5M ONLY')

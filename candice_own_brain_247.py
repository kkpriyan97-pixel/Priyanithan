from __future__ import annotations

import asyncio
import time


def install(app):
    """Run Own Brain V4 independently on every newly closed 1m candle, 24/7.

    Observation/learning only. Never calls the AI reviewer and never places a trade.
    The existing 5m checkpoint remains the only signal gate.
    """
    if getattr(app, '_candice_own_brain_247', False):
        return

    import candice_engine as engine
    from candice_strategy_v4 import build_plan

    app._candice_brain_247_last_minute = {}

    async def brain_watch_once():
        assets = await app.broker.live_assets()
        selected = list(app.selected.items())
        app.log.info('CANDICE OWN BRAIN 24/7 TICK assets=%d selected=%d', len(assets), len(selected))

        for uid, asset in selected:
            try:
                if uid in app.active:
                    app.log.info('CANDICE OWN BRAIN 24/7 SKIP pair=%s reason=trade_active', asset)
                    continue
                if asset not in assets:
                    app.log.info('CANDICE OWN BRAIN 24/7 SKIP pair=%s reason=asset_not_live', asset)
                    continue

                df, err = await app.broker.candles(
                    asset, 60, engine.ANALYSIS_CANDLE_COUNT, 360
                )
                if err or df is None or len(df) < 120:
                    app.log.info(
                        'CANDICE OWN BRAIN 24/7 WAIT pair=%s reason=%s',
                        asset, err or 'insufficient candles'
                    )
                    continue

                closed = engine.closed_1m(df)
                if len(closed) < 120:
                    app.log.info(
                        'CANDICE OWN BRAIN 24/7 WAIT pair=%s reason=insufficient closed candles count=%s',
                        asset, len(closed)
                    )
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
                plan = build_plan(
                    primary,
                    frames,
                    primary.get('candle_patterns', ())
                )

                # Record observation only. This does not create a trade outcome.
                try:
                    app.record({
                        'ts': time.time(),
                        'asset': asset,
                        'brain_247': True,
                        'candle_ts': float(closed.index[-1].timestamp()),
                        'direction': frames['1m'].get('direction', ''),
                        'pattern': plan.pattern,
                        'situation': plan.situation,
                        'next_candle_direction': plan.next_candle_direction,
                        'next_candle_timeframe': plan.next_candle_timeframe,
                        'expiry': plan.recommended_expiry,
                        'wait': bool(plan.wait),
                    })
                except Exception as exc:
                    app.log.warning('CANDICE OWN BRAIN 24/7 MEMORY OBSERVATION FAILED pair=%s: %s', asset, exc)

                app.log.info(
                    'CANDICE OWN BRAIN 24/7 CANDLE pair=%s candle=%s direction=%s pattern=%s situation=%s next=%s/%s probability_gate=adaptive expiry=%s wait=%s',
                    asset,
                    closed.index[-1].strftime('%H:%M:%S UAE'),
                    frames['1m'].get('direction', ''),
                    plan.pattern,
                    plan.situation,
                    plan.next_candle_direction,
                    plan.next_candle_timeframe,
                    plan.recommended_expiry,
                    plan.wait,
                )
            except Exception:
                app.log.exception('CANDICE OWN BRAIN 24/7 ASSET WATCH FAILED pair=%s', asset)

    async def brain_watch_loop():
        app.log.info('CANDICE OWN BRAIN 24/7 LOOP STARTED — INDEPENDENT 1M CLOSED-CANDLE LOOP')
        while True:
            try:
                await brain_watch_once()
            except Exception:
                app.log.exception('CANDICE OWN BRAIN 24/7 WATCH FAILED')

            # Wake shortly after each minute boundary so the newly closed candle is available.
            now = time.time()
            next_minute = (int(now // 60) + 1) * 60 + 2
            await asyncio.sleep(max(1.0, next_minute - time.time()))

    app._candice_own_brain_247 = True

    # Do not monkey-patch scan_once: checkpoint/runtime layers may replace it later,
    # and the base scheduler may hold a direct global reference. Run independently.
    loop = getattr(app, 'BOT_LOOP', None)
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(asyncio.create_task, brain_watch_loop())
        app._candice_own_brain_247_task_scheduled = True
        app.log.info('CANDICE OWN BRAIN 24/7 ACTIVE — INDEPENDENT LOOP SCHEDULED')
    else:
        app._candice_own_brain_247_task_scheduled = False
        app.log.warning('CANDICE OWN BRAIN 24/7 ACTIVE — LOOP NOT READY, STARTUP RETRY REQUIRED')

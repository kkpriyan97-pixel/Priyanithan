from __future__ import annotations

import asyncio
import time


def install(app):
    """Run Own Brain V4 independently on every newly closed 1m candle, 24/7.

    Observation/learning only. Never calls the AI reviewer and never places a trade.
    Telegram startup/handlers are NOT required. The existing 5m checkpoint remains
    the only signal gate.
    """
    if getattr(app, '_candice_own_brain_247', False):
        return

    import candice_engine as engine
    from candice_strategy_v4 import build_plan

    app._candice_brain_247_last_minute = {}

    def _candle_ts(index_value):
        """Return a numeric epoch timestamp for either datetime-like or numeric indexes."""
        value = index_value
        if hasattr(value, 'timestamp'):
            return float(value.timestamp())
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def brain_watch_once():
        assets = await app.broker.live_assets()
        assets = list(assets or [])
        selected = list(getattr(app, 'selected', {}).items())
        app.log.info(
            'CANDICE OWN BRAIN 24/7 TICK assets=%d selected=%d',
            len(assets), len(selected)
        )

        # Own Brain is independent from Telegram selection. Observe all currently
        # live assets; signal generation is still controlled elsewhere by the 5m gate.
        for asset in assets:
            try:
                if asset in getattr(app, 'active', set()):
                    app.log.info('CANDICE OWN BRAIN 24/7 SKIP pair=%s reason=trade_active', asset)
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

                raw_index = closed.index[-1]
                candle_ts = _candle_ts(raw_index)
                if candle_ts is None:
                    app.log.info(
                        'CANDICE OWN BRAIN 24/7 WAIT pair=%s reason=invalid candle timestamp type=%s',
                        asset, type(raw_index).__name__
                    )
                    continue

                candle_minute = int(candle_ts // 60)
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
                        'candle_ts': candle_ts,
                        'direction': frames['1m'].get('direction', ''),
                        'pattern': plan.pattern,
                        'situation': plan.situation,
                        'next_candle_direction': plan.next_candle_direction,
                        'next_candle_timeframe': plan.next_candle_timeframe,
                        'expiry': plan.recommended_expiry,
                        'wait': bool(plan.wait),
                    })
                except Exception as exc:
                    app.log.warning(
                        'CANDICE OWN BRAIN 24/7 MEMORY OBSERVATION FAILED pair=%s: %s',
                        asset, exc
                    )

                candle_label = getattr(raw_index, 'strftime', None)
                candle_text = (
                    raw_index.strftime('%H:%M:%S UAE')
                    if callable(candle_label)
                    else str(raw_index)
                )
                app.log.info(
                    'CANDICE OWN BRAIN 24/7 CANDLE pair=%s candle=%s direction=%s pattern=%s situation=%s next=%s/%s probability_gate=adaptive expiry=%s wait=%s',
                    asset,
                    candle_text,
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

            now = time.time()
            next_minute = (int(now // 60) + 1) * 60 + 2
            await asyncio.sleep(max(1.0, next_minute - time.time()))

    def _schedule_loop():
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(brain_watch_loop())
            app._candice_own_brain_247_task_scheduled = True
            app.log.info('CANDICE OWN BRAIN 24/7 ACTIVE — INDEPENDENT LOOP SCHEDULED')
            return True
        except RuntimeError:
            return False

    async def _startup_retry():
        for _ in range(120):
            if _schedule_loop():
                return
            await asyncio.sleep(1)
        app.log.error('CANDICE OWN BRAIN 24/7 STARTUP FAILED — NO RUNNING EVENT LOOP')

    app._candice_own_brain_247 = True
    app._candice_own_brain_247_task_scheduled = False

    # Prefer the currently running asyncio loop. This makes Own Brain independent
    # from Telegram initialization and avoids the old BOT_LOOP/handler dependency.
    if not _schedule_loop():
        loop = getattr(app, 'BOT_LOOP', None)
        if loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(_startup_retry(), loop)
                app.log.info('CANDICE OWN BRAIN 24/7 ACTIVE — STARTUP RETRY SCHEDULED')
            except Exception as exc:
                app.log.warning(
                    'CANDICE OWN BRAIN 24/7 STARTUP RETRY FAILED: %s', exc
                )
        else:
            app.log.warning(
                'CANDICE OWN BRAIN 24/7 ACTIVE — WAITING FOR EVENT LOOP (Telegram not required)'
            )

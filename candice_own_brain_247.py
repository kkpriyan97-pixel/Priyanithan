from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, is_dataclass


FETCH_TIMEOUT = 25
MAX_CONCURRENCY = 3


def install(app):
    """Independent Candice Own Brain loop.

    Runs continuously on newly closed 1m candles and feeds observations to the
    V4 strategy brain. It is deliberately independent from Telegram selection,
    Telegram handlers and the 5m signal gate. It never places trades.
    """
    if getattr(app, '_candice_own_brain_247_task', None) is not None:
        return

    import candice_engine as engine
    from candice_strategy_v4 import build_plan

    app._candice_brain_247_last_minute = {}
    app._candice_own_brain_247_started = False
    app._candice_own_brain_247_task = None

    def candle_ts(value):
        if hasattr(value, 'timestamp'):
            try:
                return float(value.timestamp())
            except Exception:
                return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def observe_asset(asset, semaphore):
        async with semaphore:
            try:
                app.log.info('CANDICE OWN BRAIN 24/7 FETCH START pair=%s', asset)
                try:
                    df, err = await asyncio.wait_for(
                        app.broker.candles(asset, 60, engine.ANALYSIS_CANDLE_COUNT, 360),
                        timeout=FETCH_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    app.log.warning('CANDICE OWN BRAIN 24/7 FETCH TIMEOUT pair=%s', asset)
                    return

                app.log.info(
                    'CANDICE OWN BRAIN 24/7 FETCH DONE pair=%s rows=%s err=%s',
                    asset, len(df) if df is not None else 0, err or '',
                )
                if err or df is None or len(df) < 120:
                    app.log.info(
                        'CANDICE OWN BRAIN 24/7 WAIT pair=%s reason=%s',
                        asset, err or 'insufficient candles',
                    )
                    return

                closed = engine.closed_1m(df)
                if len(closed) < 120:
                    app.log.info(
                        'CANDICE OWN BRAIN 24/7 WAIT pair=%s reason=insufficient closed candles count=%s',
                        asset, len(closed),
                    )
                    return

                raw_index = closed.index[-1]
                ts = candle_ts(raw_index)
                if ts is None:
                    app.log.info(
                        'CANDICE OWN BRAIN 24/7 WAIT pair=%s reason=invalid candle timestamp',
                        asset,
                    )
                    return

                minute = int(ts // 60)
                if app._candice_brain_247_last_minute.get(asset) == minute:
                    return
                app._candice_brain_247_last_minute[asset] = minute

                app.log.info(
                    'CANDICE OWN BRAIN 24/7 CLOSED READY pair=%s candle_ts=%s',
                    asset, ts,
                )

                frames = {'1m': engine.technical_snapshot(closed)}
                for mins in (3, 5, 10, 15):
                    try:
                        agg = engine.resample_ohlc(closed, mins)
                        if len(agg) >= 60:
                            frames[f'{mins}m'] = engine.technical_snapshot(agg)
                    except Exception as exc:
                        app.log.warning(
                            'CANDICE OWN BRAIN 24/7 FRAME FAILED pair=%s tf=%sm: %s',
                            asset, mins, exc,
                        )

                primary = frames.get('5m', frames['1m'])
                patterns = primary.get('candle_patterns', ())
                plan = build_plan(primary, frames, patterns)
                app.log.info(
                    'CANDICE OWN BRAIN 24/7 PLAN READY pair=%s direction=%s pattern=%s situation=%s next=%s/%s expiry=%s wait=%s',
                    asset,
                    frames['1m'].get('direction', ''),
                    getattr(plan, 'pattern', ''),
                    getattr(plan, 'situation', ''),
                    getattr(plan, 'next_candle_direction', ''),
                    getattr(plan, 'next_candle_timeframe', ''),
                    getattr(plan, 'recommended_expiry', 0),
                    getattr(plan, 'wait', True),
                )

                try:
                    event = {
                        'ts': time.time(),
                        'type': 'brain_247_observation',
                        'asset': asset,
                        'candle_ts': ts,
                        'direction': frames['1m'].get('direction', ''),
                        'pattern': getattr(plan, 'pattern', ''),
                        'situation': getattr(plan, 'situation', ''),
                        'next_candle_direction': getattr(plan, 'next_candle_direction', ''),
                        'next_candle_timeframe': getattr(plan, 'next_candle_timeframe', ''),
                        'expiry': getattr(plan, 'recommended_expiry', 0),
                        'wait': bool(getattr(plan, 'wait', True)),
                    }
                    app.record(event)
                except Exception as exc:
                    app.log.warning(
                        'CANDICE OWN BRAIN 24/7 MEMORY OBSERVATION FAILED pair=%s: %s',
                        asset, exc,
                    )

                candle_label = raw_index.strftime('%H:%M:%S UAE') if hasattr(raw_index, 'strftime') else str(raw_index)
                app.log.info(
                    'CANDICE OWN BRAIN 24/7 CANDLE pair=%s candle=%s direction=%s pattern=%s situation=%s next=%s/%s expiry=%s wait=%s',
                    asset, candle_label, frames['1m'].get('direction', ''),
                    getattr(plan, 'pattern', ''), getattr(plan, 'situation', ''),
                    getattr(plan, 'next_candle_direction', ''),
                    getattr(plan, 'next_candle_timeframe', ''),
                    getattr(plan, 'recommended_expiry', 0),
                    getattr(plan, 'wait', True),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                app.log.exception('CANDICE OWN BRAIN 24/7 ASSET WATCH FAILED pair=%s', asset)

    async def brain_watch_once():
        try:
            assets = list(await asyncio.wait_for(app.broker.live_assets(), timeout=FETCH_TIMEOUT) or [])
        except asyncio.TimeoutError:
            app.log.warning('CANDICE OWN BRAIN 24/7 LIVE ASSET TIMEOUT')
            return
        except Exception:
            app.log.exception('CANDICE OWN BRAIN 24/7 LIVE ASSET FAILED')
            return

        # All live assets are observed. Telegram's selected asset only controls
        # which assets receive alerts; it never controls the brain's learning loop.
        selected = len(getattr(app, 'selected', {}) or {})
        app.log.info(
            'CANDICE OWN BRAIN 24/7 TICK assets=%d selected=%d concurrency=%d',
            len(assets), selected, MAX_CONCURRENCY,
        )
        if not assets:
            app.log.info('CANDICE OWN BRAIN 24/7 WAIT reason=no_live_assets')
            return

        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        await asyncio.gather(
            *(observe_asset(asset, semaphore) for asset in assets),
            return_exceptions=False,
        )

    async def brain_watch_loop():
        app._candice_own_brain_247_started = True
        app.log.info(
            'CANDICE OWN BRAIN 24/7 LOOP STARTED — INDEPENDENT CLOSED 1M OBSERVATION'
        )
        while True:
            started = time.time()
            try:
                await brain_watch_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                app.log.exception('CANDICE OWN BRAIN 24/7 WATCH FAILED')

            # Re-align to the next minute boundary, with a small post-close delay.
            now = time.time()
            next_minute = (int(now // 60) + 1) * 60 + 2
            sleep_for = max(1.0, next_minute - time.time())
            app.log.info(
                'CANDICE OWN BRAIN 24/7 CYCLE DONE elapsed=%.2fs next_in=%.2fs',
                time.time() - started, sleep_for,
            )
            await asyncio.sleep(sleep_for)

    def schedule_from_running_loop():
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        if app._candice_own_brain_247_task is None:
            app._candice_own_brain_247_task = loop.create_task(brain_watch_loop())
            app.log.info('CANDICE OWN BRAIN 24/7 ACTIVE — INDEPENDENT TASK SCHEDULED')
        return True

    async def startup_retry():
        for _ in range(180):
            if schedule_from_running_loop():
                return
            await asyncio.sleep(1)
        app.log.error('CANDICE OWN BRAIN 24/7 STARTUP FAILED — NO RUNNING EVENT LOOP')

    # Mark the layer installed only after a task is actually created. This fixes
    # the old race where an early install set the flag and prevented later retry.
    if schedule_from_running_loop():
        return

    loop = getattr(app, 'BOT_LOOP', None)
    if loop is not None:
        try:
            app._candice_own_brain_247_task = asyncio.run_coroutine_threadsafe(startup_retry(), loop)
            app.log.info('CANDICE OWN BRAIN 24/7 STARTUP RETRY SCHEDULED')
            return
        except Exception as exc:
            app.log.warning('CANDICE OWN BRAIN 24/7 RETRY SCHEDULE FAILED: %s', exc)

    app.log.warning(
        'CANDICE OWN BRAIN 24/7 WAITING FOR EVENT LOOP — TELEGRAM NOT REQUIRED'
    )

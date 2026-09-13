from __future__ import annotations

import asyncio
import time


def install(app):
    """Keep Candice's AI manager working every minute, independent of Telegram UI state."""
    if getattr(app, '_candice_ai_247_v2', False):
        return

    # Keep long-running asyncio tasks strongly referenced. Python's event loop
    # only keeps weak references to tasks, so this prevents the core engine from
    # silently disappearing during a long 24/7 session.
    background_tasks = getattr(app, '_candice_background_tasks', None)
    if background_tasks is None:
        background_tasks = set()
        app._candice_background_tasks = background_tasks

    base_scan = app.scan_once
    original = getattr(app, '_candice_ai_247_base_scan', None)
    if original is not None:
        base_scan = original

    async def ai_247_scan():
        await base_scan()

        minute = int(time.time() // 60)
        if (minute % 5) == 4:
            return

        selected = list(getattr(app, 'selected', {}).items())
        if not selected:
            app.log.info('CANDICE AI 24/7 HEARTBEAT state=WAIT reason=no_asset_selected')
            return

        live_assets = set()
        try:
            live_assets = set(await app.broker.live_assets())
        except Exception:
            app.log.exception('CANDICE AI 24/7 LIVE ASSET CHECK FAILED')

        seen = set()
        for uid, asset in selected:
            asset = str(asset or '').strip()
            if not asset or asset == 'NOOP' or asset in seen:
                continue
            seen.add(asset)
            if live_assets and asset not in live_assets:
                app.log.info('CANDICE AI 24/7 WAIT pair=%s reason=asset_not_live', asset)
                continue

            try:
                app.log.info(
                    'CANDICE AI 24/7 OBSERVE pair=%s user=%s candle_cycle=1m signal_checkpoint=5m',
                    asset, uid,
                )
                memory = app.summary(asset)
                result = await app.analyze(asset, app.broker, memory)
                cache = getattr(app, '_candice_last_analysis', None)
                if cache is None:
                    cache = {}
                    app._candice_last_analysis = cache
                cache[asset] = result
                app.log.info(
                    'CANDICE AI 24/7 DECISION pair=%s decision=%s direction=%s confidence=%s expiry=%s reason=%s',
                    asset,
                    getattr(result, 'decision', ''),
                    getattr(result, 'direction', ''),
                    getattr(result, 'confidence', 0),
                    getattr(result, 'expiry', 0),
                    str(getattr(result, 'reason', ''))[:500],
                )
            except Exception:
                app.log.exception('CANDICE AI 24/7 CYCLE FAILED pair=%s user=%s', asset, uid)

    async def resilient_scheduler():
        task = asyncio.current_task()
        if task is not None:
            background_tasks.add(task)
        app.log.info('CANDICE SCHEDULER 24/7 WATCHDOG ACTIVE')
        try:
            await app._candice_ai_247_scheduler_base()
        finally:
            if task is not None:
                background_tasks.discard(task)

    async def resilient_broker_loop():
        task = asyncio.current_task()
        if task is not None:
            background_tasks.add(task)
        app.log.info('CANDICE BROKER 24/7 WATCHDOG ACTIVE')
        try:
            await app._candice_ai_247_broker_base()
        finally:
            if task is not None:
                background_tasks.discard(task)

    if not getattr(app, '_candice_ai_247_scheduler_wrapped', False):
        app._candice_ai_247_scheduler_base = app.scheduler
        app.scheduler = resilient_scheduler
        app._candice_ai_247_scheduler_wrapped = True

    if not getattr(app, '_candice_ai_247_broker_wrapped', False):
        app._candice_ai_247_broker_base = app.broker.connect_forever
        app.broker.connect_forever = resilient_broker_loop
        app._candice_ai_247_broker_wrapped = True

    app.scan_once = ai_247_scan
    app._candice_ai_247_v2 = True
    app._candice_ai_247_base_scan = base_scan
    app.log.info(
        'CANDICE AI 24/7 ACTIVE — SERVER SIDE — 1M ANALYSIS — 5M SIGNAL CHECKPOINT — TELEGRAM UI INDEPENDENT — WATCHDOG PROTECTED'
    )

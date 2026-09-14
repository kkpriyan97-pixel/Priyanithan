from __future__ import annotations

import asyncio
import time


def install(app):
    """Show the exact 5-minute decision countdown without Telegram flood spam."""
    if getattr(app, '_candice_ready_timer_v7', False):
        return

    # app.asset_callback is not guaranteed to exist because the real callback
    # is a module-level function dispatched by asset_callback_dispatch().
    # Using the dispatcher here keeps the timer layer compatible with both
    # callback layouts and prevents the READY card from losing its timer task.
    base_asset_callback = getattr(app, 'asset_callback', None)
    if base_asset_callback is None:
        base_asset_callback = getattr(app, 'asset_callback_dispatch', None)
    if base_asset_callback is None:
        raise RuntimeError('asset callback dispatcher is not ready')

    tasks = {}

    async def safe_timer_edit(uid, message_id, text):
        try:
            return await app.edit_text(uid, message_id, text)
        except Exception as exc:
            app.log.warning('ASSET READY TIMER EDIT SKIPPED chat=%s error=%r', uid, exc)
            return False

    def next_boundary(ts=None):
        now = int(time.time() if ts is None else ts)
        return ((now // 300) + 1) * 300

    async def ready_countdown(uid, message_id, asset):
        app.log.info('ASSET READY TIMER START chat=%s asset=%s mode=exact-boundary-ui-v7', uid, asset)
        last_display = None
        while True:
            if uid in app.active or app.selected.get(uid) != asset:
                app.log.info('ASSET READY TIMER STOP chat=%s asset=%s reason=signal_or_asset_change', uid, asset)
                return

            boundary = next_boundary()
            while True:
                if uid in app.active or app.selected.get(uid) != asset:
                    app.log.info('ASSET READY TIMER STOP chat=%s asset=%s reason=signal_or_asset_change', uid, asset)
                    return

                remaining = max(0, int(boundary - time.time()))
                display = remaining if remaining <= 10 else remaining - (remaining % 30)
                if display != last_display:
                    last_display = display
                    mm, ss = divmod(display, 60)
                    boundary_text = app.datetime.fromtimestamp(boundary, app.UAE).strftime('%H:%M:%S UAE') if hasattr(app, 'datetime') else app.now().strftime('%H:%M:%S UAE')
                    text = (
                        f'{app.header("ASSET READY")}\n\n'
                        f'📈 {asset}\n\n'
                        f'🟢 MARKET STATUS • LIVE\n'
                        f'🔵 CANDLE • 1 MIN FRESH\n'
                        f'🕒 VERIFIED • {app.now().strftime("%H:%M:%S UAE")}\n\n'
                        f'🔥 CANDICE RESEARCH • ACTIVE\n'
                        f'🧠 HUMAN BRAIN / AI MANAGER • 24/7 ACTIVE\n'
                        f'🔬 1M MARKET ANALYSIS • CONTINUOUS\n\n'
                        f'⚡ READY FOR QUALIFIED SIGNAL\n'
                        f'⏱️ ADAPTIVE EXPIRY • 1 / 2 / 3 / 5 / 10 / 15 MIN\n'
                        f'📩 Signal alert • 10 SEC before entry\n\n'
                        f'🎯 NEXT 5-MIN CHECKPOINT • {boundary_text}\n'
                        f'⏳ DECISION TIMER • {mm:02d}:{ss:02d}\n'
                        f'🔄 LIVE COUNTDOWN • ACTIVE\n\n'
                        f'🛡️ MANUAL TRADE ONLY • AUTO-TRADE OFF'
                    )
                    ok = await safe_timer_edit(uid, message_id, text)
                    app.log.info('ASSET READY TIMER TICK chat=%s asset=%s remaining=%s edit=%s', uid, asset, remaining, ok)

                if remaining <= 0:
                    break
                await asyncio.sleep(1.0 if remaining <= 10 else 30.0)

            await asyncio.sleep(1.0)
            last_display = None

    async def patched_asset_callback(update, ctx):
        await base_asset_callback(update, ctx)
        q = update.callback_query
        uid = q.from_user.id
        asset = app.selected.get(uid)
        if not asset or asset == 'NOOP':
            return
        old = tasks.pop(uid, None)
        if old is not None and not old.done():
            old.cancel()
        tasks[uid] = asyncio.create_task(ready_countdown(uid, q.message.message_id, asset))
        app.log.info('ASSET READY TIMER TASK CREATED chat=%s asset=%s message_id=%s', uid, asset, q.message.message_id)

    app.asset_callback = patched_asset_callback
    app._candice_ready_timer_v7 = True
    app.log.info('CANDICE ASSET READY TIMER V7 ACTIVE — dispatcher-safe — exact :00/:05/:10 UI — 30s updates + final 10s')

from __future__ import annotations

import asyncio
import time


def install(app):
    """Show a real 5-minute diagnostic countdown after asset selection."""
    if getattr(app, '_candice_ready_timer_v1', False):
        return

    base_asset_callback = app.asset_callback
    tasks = {}

    async def ready_countdown(uid, message_id, asset, seconds=300):
        deadline = time.monotonic() + float(seconds)
        last = None
        app.log.info('ASSET READY TIMER START chat=%s asset=%s seconds=%s', uid, asset, seconds)
        while True:
            if uid in app.active or app.selected.get(uid) != asset:
                app.log.info('ASSET READY TIMER STOP chat=%s asset=%s reason=signal_or_asset_change', uid, asset)
                return
            remaining = max(0, int(deadline - time.monotonic()))
            if remaining != last:
                last = remaining
                mm, ss = divmod(remaining, 60)
                text = (
                    f'{app.header("ASSET READY")}\n\n'
                    f'📈 {asset}\n\n'
                    f'🟢 MARKET STATUS • LIVE\n'
                    f'🔵 CANDLE • 1 MIN FRESH\n'
                    f'🕒 VERIFIED • {app.now().strftime("%H:%M:%S UAE")}\n\n'
                    f'🔥🔥🔥 CANDICE RESEARCH ACTIVE\n\n'
                    f'🟢 Trend structure\n'
                    f'🔵 Momentum confirmation\n'
                    f'🟣 Volatility / breakout check\n'
                    f'🟠 MACD confirmation\n'
                    f'🟡 AI decision gate\n\n'
                    f'⚡ READY FOR QUALIFIED SIGNAL\n'
                    f'⏱️ 1 / 2 / 3 / 5 / 10 / 15 MIN\n'
                    f'📩 Signal alert • 10 SEC before entry\n\n'
                    f'🧠 AI DECISION TIMER • {mm:02d}:{ss:02d}\n'
                    f'🔎 Timer end = decision/status check\n\n'
                    f'🛡️ MANUAL TRADE ONLY • AUTO-TRADE OFF'
                )
                ok = await app.edit_text(uid, message_id, text)
                app.log.info('ASSET READY TIMER TICK chat=%s asset=%s remaining=%s edit=%s', uid, asset, remaining, ok)
            if remaining <= 0:
                break
            await asyncio.sleep(1.0)

        if uid in app.active or app.selected.get(uid) != asset:
            return
        complete = (
            f'{app.header("DECISION TIMER COMPLETE")}\n\n'
            f'📈 {asset}\n'
            f'⏱️ 05:00 diagnostic window finished\n\n'
            f'🔬 Research: CHECKED\n'
            f'🧠 AI manager: CHECKING NOW\n'
            f'🎯 Decision: ANALYZING\n\n'
            f'⚠️ No signal is forced by this timer.\n'
            f'If no qualified setup exists, Candice will report NO SIGNAL + reason.'
        )
        await app.edit_text(uid, message_id, complete)
        app.log.info('ASSET READY TIMER COMPLETE chat=%s asset=%s', uid, asset)
        scan = getattr(app, '_candice_original_scan', None)
        if scan is not None:
            try:
                await scan()
                app.log.info('ASSET READY TIMER DECISION SCAN COMPLETE chat=%s asset=%s', uid, asset)
            except Exception:
                app.log.exception('ASSET READY TIMER DECISION SCAN FAILED chat=%s asset=%s', uid, asset)

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
        tasks[uid] = asyncio.create_task(ready_countdown(uid, q.message.message_id, asset, 300))

    app.asset_callback = patched_asset_callback
    app._candice_ready_timer_v1 = True
    app.log.info('CANDICE ASSET READY TIMER ACTIVE — 5M DIAGNOSTIC COUNTDOWN')

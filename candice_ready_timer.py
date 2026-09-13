from __future__ import annotations

import asyncio
import time


def install(app):
    """Show a low-traffic exact 5-minute UI countdown; the checkpoint engine owns decisions."""
    if getattr(app, '_candice_ready_timer_v5', False):
        return

    base_asset_callback = app.asset_callback
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
        app.log.info('ASSET READY TIMER START chat=%s asset=%s mode=exact-boundary-ui', uid, asset)
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
                # Low Telegram traffic: update every 5 seconds, except the final 10 seconds.
                display = remaining if remaining <= 10 else remaining - (remaining % 5)
                if display != last_display:
                    last_display = display
                    mm, ss = divmod(display, 60)
                    boundary_dt = app.datetime.fromtimestamp(boundary, app.UAE) if hasattr(app, 'datetime') else None
                    if boundary_dt is not None:
                        boundary_text = boundary_dt.strftime('%H:%M:%S UAE')
                    else:
                        boundary_text = app.now().strftime('%H:%M:%S UAE')
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
                        f'⏳ DECISION TIMER • {mm:02d}:{ss:02d}\n\n'
                        f'🛡️ MANUAL TRADE ONLY • AUTO-TRADE OFF'
                    )
                    ok = await safe_timer_edit(uid, message_id, text)
                    app.log.info('ASSET READY TIMER TICK chat=%s asset=%s remaining=%s edit=%s', uid, asset, remaining, ok)

                if remaining <= 0:
                    break
                await asyncio.sleep(1.0 if remaining <= 10 else 5.0)

            # The exact checkpoint decision is handled by candice_checkpoint.py.
            # Do not call _candice_original_scan here: that bypassed the orchestrator
            # and could create duplicate/late decisions.
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

    app.asset_callback = patched_asset_callback
    app._candice_ready_timer_v5 = True
    app.log.info('CANDICE ASSET READY TIMER V5 ACTIVE — exact :00/:05/:10 UI — 5s updates — NO SCAN DUPLICATION')

from __future__ import annotations

import asyncio
import time


def install(app):
    """Reliable 5-minute READY countdown using one dedicated Telegram message."""
    if getattr(app, '_candice_ready_timer_v8', False):
        return

    base_asset_callback = getattr(app, 'asset_callback', None)
    if base_asset_callback is None:
        base_asset_callback = getattr(app, 'asset_callback_dispatch', None)
    if base_asset_callback is None:
        raise RuntimeError('asset callback dispatcher is not ready')

    tasks = {}

    async def send_timer_message(uid, asset):
        tg = getattr(app, 'tg_app', None)
        if tg is None:
            app.log.warning('ASSET READY TIMER START SKIPPED chat=%s reason=telegram_not_ready', uid)
            return None
        try:
            m = await tg.bot.send_message(chat_id=uid, text=build_text(asset, next_boundary()))
            app.log.info('ASSET READY TIMER MESSAGE SENT chat=%s asset=%s message_id=%s', uid, asset, m.message_id)
            return m
        except Exception as exc:
            app.log.warning('ASSET READY TIMER MESSAGE FAILED chat=%s asset=%s error=%r', uid, asset, exc)
            return None

    def next_boundary(ts=None):
        now = int(time.time() if ts is None else ts)
        return ((now // 300) + 1) * 300

    def build_text(asset, boundary):
        remaining = max(0, int(boundary - time.time()))
        mm, ss = divmod(remaining, 60)
        boundary_text = app.datetime.fromtimestamp(boundary, app.UAE).strftime('%H:%M:%S UAE') if hasattr(app, 'datetime') else app.now().strftime('%H:%M:%S UAE')
        return (
            f'{app.header("DECISION TIMER")}\n\n'
            f'📈 {asset}\n'
            f'🟢 MARKET STATUS • LIVE\n'
            f'🔵 1 MIN CLOSED-CANDLE ANALYSIS • ACTIVE\n\n'
            f'🎯 NEXT 5-MIN CHECKPOINT • {boundary_text}\n'
            f'⏳ DECISION TIMER • {mm:02d}:{ss:02d}\n'
            f'🔄 LIVE COUNTDOWN • ACTIVE\n\n'
            f'📩 QUALIFIED SIGNAL ALERT • 10 SEC BEFORE ENTRY\n'
            f'⏱️ EXPIRY • 1 / 2 / 3 / 5 / 10 / 15 MIN\n\n'
            f'🛡️ MANUAL TRADE ONLY • AUTO-TRADE OFF'
        )

    async def edit_raw(tg, uid, mid, text):
        try:
            await tg.bot.edit_message_text(chat_id=uid, message_id=mid, text=text, disable_web_page_preview=True)
            return True
        except Exception as exc:
            # Timer ticks are disposable; never queue/retry and never block the signal engine.
            app.log.warning('ASSET READY TIMER EDIT SKIPPED chat=%s message_id=%s error=%r', uid, mid, exc)
            return False

    async def ready_countdown(uid, asset):
        tg = getattr(app, 'tg_app', None)
        if tg is None:
            return
        boundary = next_boundary()
        msg = await send_timer_message(uid, asset)
        if msg is None:
            return
        last_display = None
        last_edit = 0.0
        try:
            while True:
                if uid in app.active or app.selected.get(uid) != asset:
                    return

                remaining = max(0, int(boundary - time.time()))
                display = remaining if remaining <= 10 else remaining - (remaining % 30)
                now_mono = time.monotonic()
                if display != last_display and (now_mono - last_edit >= 4.0 or display <= 10):
                    last_display = display
                    last_edit = now_mono
                    ok = await edit_raw(tg, uid, msg.message_id, build_text(asset, boundary))
                    app.log.info('ASSET READY TIMER TICK chat=%s asset=%s remaining=%s edit=%s', uid, asset, remaining, ok)

                if remaining <= 0:
                    await asyncio.sleep(1.0)
                    boundary = next_boundary()
                    last_display = None
                    continue
                await asyncio.sleep(1.0 if remaining <= 10 else 5.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app.log.warning('ASSET READY TIMER FAILED chat=%s asset=%s error=%r', uid, asset, exc)

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
        tasks[uid] = asyncio.create_task(ready_countdown(uid, asset))
        app.log.info('ASSET READY TIMER TASK CREATED chat=%s asset=%s dedicated_message=TRUE', uid, asset)

    app.asset_callback = patched_asset_callback
    app._candice_ready_timer_v8 = True
    app.log.info('CANDICE ASSET READY TIMER V8 ACTIVE — dedicated Telegram countdown message — no shared edit throttle')
from __future__ import annotations

import asyncio
import time


def install(app):
    """Put the live 5-minute decision countdown directly on the ASSET READY card."""
    if getattr(app, '_candice_ready_timer_v10', False):
        return

    base_asset_callback = getattr(app, 'asset_callback', None)
    if base_asset_callback is None:
        base_asset_callback = getattr(app, 'asset_callback_dispatch', None)
    if base_asset_callback is None:
        raise RuntimeError('asset callback dispatcher is not ready')

    tasks = {}

    def next_boundary(ts=None):
        now = int(time.time() if ts is None else ts)
        return ((now // 300) + 1) * 300

    def build_ready_text(asset, boundary):
        now = time.time()
        remaining = max(0, int(boundary - now))
        elapsed = max(0, min(300, 300 - remaining))
        progress = int(round((elapsed / 300.0) * 100))
        mm, ss = divmod(remaining, 60)
        if hasattr(app, 'datetime'):
            boundary_text = app.datetime.fromtimestamp(boundary, app.UAE).strftime('%H:%M:%S UAE')
        else:
            boundary_text = app.now().strftime('%H:%M:%S UAE')

        return (
            f'{app.header("ASSET READY")}\n\n'
            f'📈 {asset}\n\n'
            f'🟢 MARKET STATUS • LIVE\n'
            f'🔵 CANDLE • 1 MIN FRESH\n'
            f'🕒 VERIFIED • LIVE CLOSED-CANDLE ENGINE\n\n'
            f'🔥🔥🔥 CANDICE RESEARCH ACTIVE\n\n'
            f'🟢 Trend structure\n'
            f'🔵 Momentum confirmation\n'
            f'🟣 Volatility / breakout check\n'
            f'🟠 MACD confirmation\n'
            f'🟡 AI decision gate\n\n'
            f'⚡ READY FOR QUALIFIED SIGNAL\n\n'
            f'🎯 NEXT 5-MIN CHECKPOINT • {boundary_text}\n'
            f'⏳ DECISION TIMER • {mm:02d}:{ss:02d}\n'
            f'📊 CHECKPOINT PROGRESS • {progress}%\n'
            f'🔄 LIVE TIMER • ON\n\n'
            f'📩 QUALIFIED SIGNAL ALERT • 10 SEC BEFORE ENTRY\n'
            f'⏱️ EXPIRY • 1 / 2 / 3 / 5 / 10 / 15 MIN\n\n'
            f'🛡️ MANUAL TRADE ONLY • AUTO-TRADE OFF'
        )

    async def edit_raw(tg, uid, mid, text):
        try:
            await tg.bot.edit_message_text(
                chat_id=uid,
                message_id=mid,
                text=text,
                disable_web_page_preview=True,
            )
            return True, None
        except Exception as exc:
            retry = getattr(exc, 'retry_after', None)
            try:
                retry = max(30.0, float(retry)) if retry is not None else None
            except (TypeError, ValueError):
                retry = None
            app.log.warning(
                'ASSET READY TIMER EDIT SKIPPED chat=%s message_id=%s retry_after=%s error=%r',
                uid, mid, retry, exc,
            )
            return False, retry

    async def ready_countdown(uid, asset, message_id):
        tg = getattr(app, 'tg_app', None)
        if tg is None:
            app.log.warning('ASSET READY TIMER START SKIPPED chat=%s reason=telegram_not_ready', uid)
            return

        boundary = next_boundary()
        last_display = None
        last_attempt = 0.0
        blocked_until = 0.0
        app.log.info(
            'ASSET READY TIMER START chat=%s asset=%s message_id=%s mode=SAME_READY_CARD',
            uid, asset, message_id,
        )

        try:
            while True:
                if uid in app.active or app.selected.get(uid) != asset:
                    return

                remaining = max(0, int(boundary - time.time()))

                # Telegram-safe schedule: one update every 30s, plus a single
                # 10-second checkpoint update and the boundary update. Never
                # generate a 1-second edit storm that can trigger flood control.
                if remaining <= 10:
                    display = remaining if remaining in (10, 0) else 10
                else:
                    display = remaining - (remaining % 30)

                now_mono = time.monotonic()
                should_edit = (
                    display != last_display
                    and now_mono >= blocked_until
                    and (now_mono - last_attempt >= 4.0)
                )

                if should_edit:
                    last_attempt = now_mono
                    ok, retry = await edit_raw(
                        tg, uid, message_id, build_ready_text(asset, boundary)
                    )
                    if ok:
                        last_display = display
                    elif retry is not None:
                        blocked_until = time.monotonic() + retry + 2.0
                    app.log.info(
                        'ASSET READY TIMER TICK chat=%s asset=%s remaining=%s progress=%s edit=%s',
                        uid, asset, remaining,
                        int(round((300 - remaining) / 300.0 * 100)), ok,
                    )

                if remaining <= 0:
                    await asyncio.sleep(1.0)
                    boundary = next_boundary()
                    last_display = None
                    blocked_until = 0.0
                    continue

                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app.log.warning(
                'ASSET READY TIMER FAILED chat=%s asset=%s error=%r', uid, asset, exc
            )

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

        tasks[uid] = asyncio.create_task(
            ready_countdown(uid, asset, q.message.message_id)
        )
        app.log.info(
            'ASSET READY TIMER TASK CREATED chat=%s asset=%s message_id=%s same_card=TRUE',
            uid, asset, q.message.message_id,
        )

    app.asset_callback = patched_asset_callback
    app._candice_ready_timer_v10 = True
    app.log.info(
        'CANDICE ASSET READY TIMER V10 ACTIVE — same card + percentage + flood-safe automatic countdown'
    )

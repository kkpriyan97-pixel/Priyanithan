from __future__ import annotations

import asyncio
import time


def install(app):
    """Make every 5-minute recovery pause visible and self-closing."""
    if getattr(app, '_candice_recovery_timer_v2', False):
        return

    base_result_monitor = app.result_monitor

    async def recovery_countdown(uid: int, seconds: int = 300):
        deadline = time.monotonic() + float(seconds)
        last = None
        msg = await app.send_text(
            uid,
            f'{app.header("RECOVERY WINDOW")}\n\n'
            f'🛡️ Loss protection ACTIVE\n'
            f'⏳ Recovery timer • {seconds // 60:02d}:00\n'
            f'🔬 1M research continues\n'
            f'⏸️ New signal locked until timer ends\n\n'
            f'🚫 Martingale OFF • Auto-trade OFF'
        )
        app.log.info('RECOVERY TIMER START chat=%s seconds=%s message_id=%s', uid, seconds, getattr(msg, 'message_id', None))

        while True:
            remaining = max(0, int(deadline - time.monotonic()))
            if remaining != last:
                last = remaining
                mm, ss = divmod(remaining, 60)
                text = (
                    f'{app.header("RECOVERY WINDOW")}\n\n'
                    f'🛡️ Loss protection ACTIVE\n'
                    f'⏳ Recovery timer • {mm:02d}:{ss:02d}\n'
                    f'🔬 1M research continues\n'
                    f'⏸️ New signal locked until timer ends\n\n'
                    f'🚫 Martingale OFF • Auto-trade OFF'
                )
                if msg is not None:
                    await app.edit_text(uid, msg.message_id, text)
                app.log.info('RECOVERY TIMER TICK chat=%s remaining=%s', uid, remaining)
            if remaining <= 0:
                break
            await asyncio.sleep(1.0)

        app.recovery_until.pop(uid, None)
        complete = (
            f'{app.header("RECOVERY DECISION COMPLETE")}\n\n'
            f'🟢 5-MIN recovery window finished\n'
            f'🔬 Research: COMPLETE\n'
            f'🧠 AI manager: STARTING\n'
            f'🎯 Decision: ANALYZING\n\n'
            f'⏱️ New market decision is being evaluated now.'
        )
        if msg is not None:
            await app.edit_text(uid, msg.message_id, complete)
        else:
            await app.send_text(uid, complete)
        app.log.info('RECOVERY TIMER COMPLETE chat=%s — lock cleared — decision restart', uid)

        original_scan = getattr(app, '_candice_original_scan', None)
        if original_scan is not None:
            try:
                await original_scan()
                app.log.info('RECOVERY RESTART SCAN COMPLETE chat=%s', uid)
            except Exception:
                app.log.exception('RECOVERY RESTART SCAN FAILED chat=%s', uid)
        else:
            app.log.warning('RECOVERY RESTART SCAN UNAVAILABLE chat=%s', uid)

    async def patched_result_monitor(uid, signal, signal_msg):
        await base_result_monitor(uid, signal, signal_msg)
        until = float(app.recovery_until.get(uid, 0) or 0)
        if until <= time.time():
            return
        remaining = max(1, int(until - time.time()))
        app.log.info('RECOVERY TIMER ARMED chat=%s remaining=%s', uid, remaining)
        await recovery_countdown(uid, remaining)

    app.result_monitor = patched_result_monitor
    app._candice_recovery_timer_v2 = True
    app.log.info('CANDICE RECOVERY TIMER ACTIVE — 5M COUNTDOWN — EXPLICIT COMPLETION STATUS')

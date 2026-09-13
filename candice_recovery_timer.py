from __future__ import annotations

import asyncio
import time


def install(app):
    """Make every 5-minute recovery pause visible and self-closing."""
    if getattr(app, '_candice_recovery_timer_v1', False):
        return

    base_result_monitor = app.result_monitor

    async def recovery_countdown(uid: int, seconds: int = 300):
        deadline = time.monotonic() + float(seconds)
        last = None
        app.log.info('RECOVERY TIMER START chat=%s seconds=%s', uid, seconds)

        while True:
            remaining = max(0, int(deadline - time.monotonic()))
            if remaining != last:
                last = remaining
                mm, ss = divmod(remaining, 60)
                await app.send_text(
                    uid,
                    f'{app.header("RECOVERY WINDOW")}\n\n'
                    f'🛡️ Loss protection ACTIVE\n'
                    f'⏳ Recovery timer • {mm:02d}:{ss:02d}\n'
                    f'🔬 1M research continues\n'
                    f'⏸️ New signal locked until timer ends\n\n'
                    f'🚫 Martingale OFF • Auto-trade OFF'
                )
                app.log.info('RECOVERY TIMER TICK chat=%s remaining=%s', uid, remaining)
            if remaining <= 0:
                break
            await asyncio.sleep(1.0)

        # Remove the lock before the immediate post-recovery decision.
        app.recovery_until.pop(uid, None)
        await app.send_text(
            uid,
            f'{app.header("RECOVERY DECISION COMPLETE")}\n\n'
            f'🟢 5-MIN recovery window finished\n'
            f'🔬 Research: COMPLETE\n'
            f'🧠 AI manager: STARTING\n'
            f'🎯 Decision: ANALYZING\n\n'
            f'⏱️ New market decision is being evaluated now.'
        )
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
        # The base monitor sets recovery_until only after a LOSS.
        until = float(app.recovery_until.get(uid, 0) or 0)
        if until <= time.time():
            return
        remaining = max(1, int(until - time.time()))
        app.log.info('RECOVERY TIMER ARMED chat=%s remaining=%s', uid, remaining)
        await recovery_countdown(uid, remaining)

    app.result_monitor = patched_result_monitor
    app._candice_recovery_timer_v1 = True
    app.log.info('CANDICE RECOVERY TIMER ACTIVE — 5M COUNTDOWN — EXPLICIT COMPLETION STATUS')

from __future__ import annotations

import asyncio
import time


def install(app):
    """Run recovery completion through the live Candice orchestrator."""
    if getattr(app, '_candice_recovery_timer_v3', False):
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
            f'🔬 1M AI research continues\n'
            f'⏸️ New signal locked until timer ends\n\n'
            f'🚫 Martingale OFF • Auto-trade OFF'
        )
        app.log.info('RECOVERY TIMER START chat=%s seconds=%s message_id=%s', uid, seconds, getattr(msg, 'message_id', None))

        # Long recovery timers do not need Telegram edits every second.
        # Keep the server-side timer continuous and update the UI every 5s.
        while True:
            remaining = max(0, int(deadline - time.monotonic()))
            display_remaining = remaining - (remaining % 5) if remaining > 10 else remaining
            if display_remaining != last:
                last = display_remaining
                mm, ss = divmod(display_remaining, 60)
                text = (
                    f'{app.header("RECOVERY WINDOW")}\n\n'
                    f'🛡️ Loss protection ACTIVE\n'
                    f'⏳ Recovery timer • {mm:02d}:{ss:02d}\n'
                    f'🔬 1M AI research continues\n'
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
            f'⏱️ Fresh 1M/3M/5M/10M/15M analysis is running now.'
        )
        if msg is not None:
            await app.edit_text(uid, msg.message_id, complete)
        else:
            await app.send_text(uid, complete)
        app.log.info('RECOVERY TIMER COMPLETE chat=%s — lock cleared — orchestrator restart', uid)

        # IMPORTANT: use the patched app.analyze() path, not the original
        # app.scan_once(), so recovery restart gets the same Human Brain + AI
        # Orchestrator + safe expiry-repair pipeline as normal analysis.
        try:
            asset = app.selected.get(uid)
            if not asset:
                app.log.warning('RECOVERY RESTART SKIPPED chat=%s reason=no_asset_selected', uid)
                return
            if uid in app.active:
                app.log.info('RECOVERY RESTART SKIPPED chat=%s reason=trade_active', uid)
                return
            if hasattr(app, 'broker'):
                live_assets = set(await app.broker.live_assets())
                if asset not in live_assets:
                    app.log.info('RECOVERY RESTART SKIPPED chat=%s pair=%s reason=asset_not_live', uid, asset)
                    return
            memory = app.summary(asset)
            result = await app.analyze(asset, app.broker, memory)
            app._candice_last_analysis[asset] = result
            app.log.info(
                'RECOVERY RESTART ORCHESTRATOR chat=%s pair=%s decision=%s direction=%s confidence=%s expiry=%s reason=%s',
                uid, asset, getattr(result, 'decision', ''), getattr(result, 'direction', ''),
                getattr(result, 'confidence', 0), getattr(result, 'expiry', 0), getattr(result, 'reason', '')
            )
        except Exception:
            app.log.exception('RECOVERY RESTART ORCHESTRATOR FAILED chat=%s', uid)

    async def patched_result_monitor(uid, signal, signal_msg):
        await base_result_monitor(uid, signal, signal_msg)
        until = float(app.recovery_until.get(uid, 0) or 0)
        if until <= time.time():
            return
        remaining = max(1, int(until - time.time()))
        app.log.info('RECOVERY TIMER ARMED chat=%s remaining=%s', uid, remaining)
        await recovery_countdown(uid, remaining)

    app.result_monitor = patched_result_monitor
    app._candice_recovery_timer_v3 = True
    app.log.info('CANDICE RECOVERY TIMER V3 ACTIVE — 5M UI TIMER — ORCHESTRATOR RESTART')

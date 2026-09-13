from __future__ import annotations

import asyncio
import time


def install(app):
    """Show a resilient 5-minute signal checkpoint while AI keeps running continuously."""
    if getattr(app, '_candice_ready_timer_v4', False):
        return

    base_asset_callback = app.asset_callback
    tasks = {}

    async def safe_timer_edit(uid, message_id, text):
        for attempt in range(3):
            try:
                return await app.edit_text(uid, message_id, text)
            except Exception as exc:
                app.log.warning('ASSET READY TIMER EDIT RETRY chat=%s attempt=%s error=%r', uid, attempt + 1, exc)
                if attempt < 2:
                    retry_after = getattr(exc, 'retry_after', None)
                    try:
                        delay = max(1.5, float(retry_after or 0))
                    except (TypeError, ValueError):
                        delay = 1.5
                    await asyncio.sleep(delay)
        app.log.error('ASSET READY TIMER EDIT SKIPPED chat=%s message=%s', uid, message_id)
        return False

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
                    f'🧠 AI MANAGER • 24/7 ACTIVE\n\n'
                    f'⚡ READY FOR QUALIFIED SIGNAL\n'
                    f'⏱️ 1 / 2 / 3 / 5 / 10 / 15 MIN\n'
                    f'📩 Signal alert • 10 SEC before entry\n\n'
                    f'⏱️ 5-MIN SIGNAL CHECKPOINT • {mm:02d}:{ss:02d}\n'
                    f'🔬 AI continues analyzing every 1 MIN — timer is UI/status only\n\n'
                    f'🛡️ MANUAL TRADE ONLY • AUTO-TRADE OFF'
                )
                ok = await safe_timer_edit(uid, message_id, text)
                app.log.info('ASSET READY TIMER TICK chat=%s asset=%s remaining=%s edit=%s', uid, asset, remaining, ok)
            if remaining <= 0:
                break
            await asyncio.sleep(1.0)

        if uid in app.active or app.selected.get(uid) != asset:
            return

        complete = (
            f'{app.header("5-MIN SIGNAL CHECKPOINT")}\n\n'
            f'📈 {asset}\n'
            f'⏱️ Checkpoint reached\n\n'
            f'🔬 1M research: CONTINUOUS\n'
            f'🧠 AI manager: 24/7 ACTIVE\n'
            f'🎯 Signal gate: CHECKING NOW\n\n'
            f'⏳ Waiting for the actual qualified decision result...'
        )
        await safe_timer_edit(uid, message_id, complete)
        app.log.info('ASSET READY TIMER COMPLETE chat=%s asset=%s — SIGNAL CHECKPOINT SCAN START', uid, asset)

        scan = getattr(app, '_candice_original_scan', None)
        if scan is None:
            await safe_timer_edit(uid, message_id, complete.replace('ACTIVE', 'UNAVAILABLE').replace('CHECKING NOW', 'UNAVAILABLE').replace('⏳ Waiting for the actual qualified decision result...', '❌ Decision scanner is unavailable.'))
            app.log.error('ASSET READY TIMER DECISION SCAN UNAVAILABLE chat=%s asset=%s', uid, asset)
            return

        try:
            await scan()
            await asyncio.sleep(0.25)
        except Exception as exc:
            app.log.exception('ASSET READY TIMER DECISION SCAN FAILED chat=%s asset=%s', uid, asset)
            await safe_timer_edit(uid, message_id, (
                f'{app.header("DECISION ERROR")}\n\n📈 {asset}\n\n'
                f'❌ AI decision cycle failed\n'
                f'🧠 Reason: {str(exc)[:500]}\n\n'
                f'🛡️ No trade was executed.'
            ))
            return

        result = getattr(app, '_candice_last_analysis', {}).get(asset)
        if result is None:
            await safe_timer_edit(uid, message_id, (
                f'{app.header("NO SIGNAL")}\n\n📈 {asset}\n\n'
                f'⚪ AI decision completed, but no analysis result was recorded.\n'
                f'🔎 Check: CANDICE AI DECISION RESULT log\n\n'
                f'🛡️ No trade was executed.'
            ))
            app.log.warning('ASSET READY TIMER NO DECISION TRACE chat=%s asset=%s', uid, asset)
            return

        decision = str(getattr(result, 'decision', '')).upper()
        direction = str(getattr(result, 'direction', '') or '').upper()
        confidence = int(getattr(result, 'confidence', 0) or 0)
        expiry = int(getattr(result, 'expiry', 0) or 0)
        reason = str(getattr(result, 'reason', '') or 'No qualifying setup')

        if decision == 'APPROVE':
            status = (
                f'{app.header("AI DECISION APPROVED")}\n\n'
                f'📈 {asset}\n\n'
                f'🟢 Direction: {direction}\n'
                f'🎯 Confidence: {confidence}%\n'
                f'⏱️ Expiry: {expiry} MIN\n'
                f'🧠 AI manager: APPROVED\n\n'
                f'🔎 {reason[:700]}\n\n'
                f'⚡ Signal dispatch is being checked.\n'
                f'🛡️ MANUAL TRADE ONLY • AUTO-TRADE OFF'
            )
        else:
            status = (
                f'{app.header("NO SIGNAL")}\n\n'
                f'📈 {asset}\n\n'
                f'⚪ AI manager: WAIT / REJECT\n'
                f'🎯 Confidence: {confidence}%\n'
                f'⏱️ Candidate expiry: {expiry or "NONE"}\n\n'
                f'🔎 Reason: {reason[:900]}\n\n'
                f'🚫 No signal was forced.\n'
                f'🛡️ MANUAL TRADE ONLY • AUTO-TRADE OFF'
            )
        await safe_timer_edit(uid, message_id, status)
        app.log.info('ASSET READY TIMER DECISION REPORTED chat=%s asset=%s decision=%s direction=%s confidence=%s expiry=%s', uid, asset, decision, direction, confidence, expiry)

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
    app._candice_ready_timer_v4 = True
    app.log.info('CANDICE ASSET READY TIMER ACTIVE — 5M CHECKPOINT — AI 24/7 STATUS — DECISION RESULT REPORTING')

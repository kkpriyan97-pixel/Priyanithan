from __future__ import annotations
import asyncio
import time
from datetime import datetime


def install(app):
    if getattr(app, '_candice_checkpoint_v2', False):
        return

    base_scan = app.scan_once

    async def no_signal_timer(uid, checkpoint_ts, reason):
        try:
            next_checkpoint = checkpoint_ts + 300
            msg = await app.send_text(
                uid,
                f'{app.header("5-MIN CHECKPOINT")}\n\n'
                f'🔴 NO SIGNAL\n'
                f'🕒 Checkpoint • {datetime.fromtimestamp(checkpoint_ts, app.UAE).strftime("%H:%M:%S UAE")}\n'
                f'❌ Reason • {reason}\n'
                f'⏭️ Next checkpoint • {datetime.fromtimestamp(next_checkpoint, app.UAE).strftime("%H:%M:%S UAE")}\n'
                f'⏳ Next decision timer • 05:00\n\n'
                f'🧠 AI 24/7 • ACTIVE\n🚫 No forced/unsafe signal'
            )
            last = None
            while True:
                remaining = max(0, int(next_checkpoint - time.time()))
                display = remaining - (remaining % 5) if remaining > 10 else remaining
                if display != last:
                    last = display
                    mm, ss = divmod(display, 60)
                    text = (
                        f'{app.header("5-MIN CHECKPOINT")}\n\n'
                        f'🔴 NO SIGNAL\n'
                        f'🕒 Last checkpoint • {datetime.fromtimestamp(checkpoint_ts, app.UAE).strftime("%H:%M:%S UAE")}\n'
                        f'❌ Reason • {reason}\n'
                        f'⏭️ Next checkpoint • {datetime.fromtimestamp(next_checkpoint, app.UAE).strftime("%H:%M:%S UAE")}\n'
                        f'⏳ Next decision timer • {mm:02d}:{ss:02d}\n\n'
                        f'🧠 AI 24/7 • ACTIVE\n🚫 No forced/unsafe signal'
                    )
                    if msg is not None:
                        await app.edit_text(uid, msg.message_id, text)
                if remaining <= 0:
                    break
                await asyncio.sleep(1)
        except Exception:
            app.log.exception('CANDICE NO-SIGNAL TIMER FAILED chat=%s', uid)

    async def checkpoint_scan():
        app.reset_daily()
        checkpoint_ts = float(((int(time.time()) // 300) + 1) * 300)
        if app.daily['losses'] >= app.MAX_DAILY_LOSSES or app.daily['streak'] >= app.MAX_STREAK:
            reason = f'RISK STOP losses={app.daily["losses"]}/{app.MAX_DAILY_LOSSES} streak={app.daily["streak"]}/{app.MAX_STREAK}'
            app.log.warning('CANDICE 5M CHECKPOINT BLOCKED reason=%s', reason)
            for uid in list(app.selected):
                asyncio.create_task(no_signal_timer(uid, checkpoint_ts, reason))
            return

        assets = await app.broker.live_assets()
        app.log.info('CANDICE 5M CHECKPOINT START target=%s assets=%d users=%d', datetime.fromtimestamp(checkpoint_ts, app.UAE).strftime('%H:%M:%S UAE'), len(assets), len(app.users))

        for uid, asset in list(app.selected.items()):
            if uid in app.active:
                app.log.info('CANDICE 5M NO SIGNAL chat=%s pair=%s reason=trade_active', uid, asset)
                continue
            if asset not in assets:
                reason = 'asset_not_live'
                app.log.info('CANDICE 5M NO SIGNAL chat=%s pair=%s reason=%s', uid, asset, reason)
                asyncio.create_task(no_signal_timer(uid, checkpoint_ts, reason))
                continue
            if app.recovery_until.get(uid, 0) > time.time():
                reason = 'recovery_protection_active'
                app.log.info('CANDICE 5M NO SIGNAL chat=%s pair=%s reason=%s', uid, asset, reason)
                asyncio.create_task(no_signal_timer(uid, checkpoint_ts, reason))
                continue

            try:
                memory = app.summary(asset)
                result = await app.analyze(asset, app.broker, memory)
                app._candice_last_analysis[asset] = result
                decision = getattr(result, 'decision', '')
                direction = getattr(result, 'direction', '')
                expiry = int(getattr(result, 'expiry', 0) or 0)
                confidence = int(getattr(result, 'confidence', 0) or 0)
                reason = str(getattr(result, 'reason', '') or 'No qualifying setup')
                app.log.info('CANDICE 5M CHECKPOINT RESULT chat=%s pair=%s decision=%s direction=%s confidence=%s expiry=%s reason=%s', uid, asset, decision, direction, confidence, expiry, reason)

                if decision != 'APPROVE':
                    asyncio.create_task(no_signal_timer(uid, checkpoint_ts, reason))
                    continue
                if expiry not in {1, 2, 3, 5, 10, 15}:
                    reason = f'invalid_expiry={expiry}'
                    asyncio.create_task(no_signal_timer(uid, checkpoint_ts, reason))
                    continue

                # Analysis may finish before the exact entry window. Hold the
                # qualified decision until the 10-second pre-entry window.
                wait_to_entry = checkpoint_ts - time.time()
                if wait_to_entry > app.PRE_ENTRY_SECONDS:
                    await asyncio.sleep(max(0.0, wait_to_entry - app.PRE_ENTRY_SECONDS))
                wait = checkpoint_ts - time.time()
                if wait > app.PRE_ENTRY_SECONDS + 2 or wait < 1:
                    reason = f'checkpoint_timing_missed seconds_to_entry={wait:.1f}'
                    app.log.info('CANDICE 5M NO SIGNAL chat=%s pair=%s reason=%s', uid, asset, reason)
                    asyncio.create_task(no_signal_timer(uid, checkpoint_ts, reason))
                    continue

                df, err = await app.broker.candles(asset, 60, 60, 120)
                if err or df is None or df.empty:
                    reason = f'fresh_candle_failed: {err or "empty"}'
                    app.log.info('CANDICE 5M NO SIGNAL chat=%s pair=%s reason=%s', uid, asset, reason)
                    asyncio.create_task(no_signal_timer(uid, checkpoint_ts, reason))
                    continue
                row = app.broker.closed_candle(df, time.time(), 60)
                if row is None:
                    reason = 'no_closed_1m_candle'
                    app.log.info('CANDICE 5M NO SIGNAL chat=%s pair=%s reason=%s', uid, asset, reason)
                    asyncio.create_task(no_signal_timer(uid, checkpoint_ts, reason))
                    continue
                entry = float(row.close)
                candle_ts = float(row.timestamp)
                key = f'{uid}:{asset}:{int(candle_ts)}'
                if key in app.sent_keys:
                    reason = 'duplicate_candle_signal_blocked'
                    asyncio.create_task(no_signal_timer(uid, checkpoint_ts, reason))
                    continue

                msg = await app.send_signal_card(uid, asset, direction, checkpoint_ts, candle_ts, expiry, confidence, getattr(result, 'timeframe', '1m+3m+5m+10m+15m'), getattr(result, 'evidence', ()))
                if msg is None:
                    reason = 'telegram_signal_delivery_failed'
                    asyncio.create_task(no_signal_timer(uid, checkpoint_ts, reason))
                    continue
                signal = app.Signal(asset, direction, confidence, expiry, entry, checkpoint_ts, candle_ts, getattr(result, 'timeframe', '1m+3m+5m+10m+15m'), reason, getattr(result, 'evidence', ()))
                app.active[uid] = signal
                app.sent_keys.add(key)
                asyncio.create_task(app.result_monitor(uid, signal, msg))
                app.log.info('CANDICE 5M SIGNAL LOCKED chat=%s pair=%s entry=%s expiry=%s confidence=%s', uid, asset, datetime.fromtimestamp(checkpoint_ts, app.UAE).strftime('%H:%M:%S UAE'), expiry, confidence)
            except Exception as exc:
                reason = f'checkpoint_exception={exc}'
                app.log.exception('CANDICE 5M CHECKPOINT FAILED chat=%s pair=%s', uid, asset)
                asyncio.create_task(no_signal_timer(uid, checkpoint_ts, reason))

    async def fixed_scheduler():
        app.log.info('CANDICE FIXED 5M SCHEDULER V2 ACTIVE — checkpoints at :00/:05/:10/... — analysis starts 30s before boundary')
        while True:
            now = time.time()
            minute = int(now // 60)
            next_boundary = ((minute // 5) + 1) * 5 * 60
            wake = next_boundary - 30
            if wake <= now:
                wake += 300
            await asyncio.sleep(max(0.2, wake - time.time()))
            try:
                await checkpoint_scan()
            except Exception:
                app.log.exception('CANDICE FIXED 5M CHECKPOINT LOOP FAILED')

    async def wrapped_scan():
        minute = int(time.time() // 60)
        if minute % 5 == 4:
            return
        await base_scan()

    app.scan_once = wrapped_scan
    app.scheduler = fixed_scheduler
    app._candice_checkpoint_v2 = True
    app.log.info('CANDICE CHECKPOINT V2 ACTIVE — 30s analysis lead + exact 10s entry window + no-signal countdown')

from __future__ import annotations

import time



def install(app):
    """Keep Candice's AI manager working every minute, independent of Telegram UI state.

    The existing scan wrapper remains responsible for 5-minute signal eligibility.
    This layer adds real server-side AI analysis on every other minute as well.
    It never dispatches a signal itself and never enables auto-trading.
    """
    if getattr(app, '_candice_ai_247_v1', False):
        return

    base_scan = app.scan_once
    original = getattr(app, '_candice_ai_247_base_scan', None)
    if original is not None:
        base_scan = original

    async def ai_247_scan():
        # The normal runtime wrapper owns the 5-minute signal slot. Let it run
        # unchanged so all existing duplicate/risk/10-second entry gates remain.
        await base_scan()

        minute = int(time.time() // 60)
        if (minute % 5) == 4:
            return

        selected = list(getattr(app, 'selected', {}).items())
        if not selected:
            app.log.info('CANDICE AI 24/7 HEARTBEAT state=WAIT reason=no_asset_selected')
            return

        live_assets = set()
        try:
            live_assets = set(await app.broker.live_assets())
        except Exception:
            app.log.exception('CANDICE AI 24/7 LIVE ASSET CHECK FAILED')

        seen = set()
        for uid, asset in selected:
            asset = str(asset or '').strip()
            if not asset or asset == 'NOOP' or asset in seen:
                continue
            seen.add(asset)
            if live_assets and asset not in live_assets:
                app.log.info('CANDICE AI 24/7 WAIT pair=%s reason=asset_not_live', asset)
                continue

            try:
                app.log.info(
                    'CANDICE AI 24/7 OBSERVE pair=%s user=%s candle_cycle=1m signal_checkpoint=5m',
                    asset, uid,
                )
                memory = app.summary(asset)
                result = await app.analyze(asset, app.broker, memory)
                cache = getattr(app, '_candice_last_analysis', None)
                if cache is None:
                    cache = {}
                    app._candice_last_analysis = cache
                cache[asset] = result
                app.log.info(
                    'CANDICE AI 24/7 DECISION pair=%s decision=%s direction=%s confidence=%s expiry=%s reason=%s',
                    asset,
                    getattr(result, 'decision', ''),
                    getattr(result, 'direction', ''),
                    getattr(result, 'confidence', 0),
                    getattr(result, 'expiry', 0),
                    str(getattr(result, 'reason', ''))[:500],
                )
            except Exception:
                app.log.exception('CANDICE AI 24/7 CYCLE FAILED pair=%s user=%s', asset, uid)

    app.scan_once = ai_247_scan
    app._candice_ai_247_v1 = True
    app._candice_ai_247_base_scan = base_scan
    app.log.info(
        'CANDICE AI 24/7 ACTIVE — SERVER SIDE — 1M ANALYSIS — 5M SIGNAL CHECKPOINT — TELEGRAM UI INDEPENDENT'
    )

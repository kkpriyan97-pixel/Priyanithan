from __future__ import annotations

import asyncio
import time


def install(app):
    """Telegram edit guard with a safe cadence for disposable READY updates."""
    if getattr(app, '_candice_telegram_ratefix_v8', False):
        return

    original_edit_text = app.edit_text
    original_edit_card = app.edit_card
    locks = {}
    last_edit = {}
    cooldown_until = {}
    text_interval = 30.0
    ready_interval = 5.0
    card_trade_interval = 15.0
    card_pre_interval = 5.0

    def lock_for(chat_id):
        return locks.setdefault(chat_id, asyncio.Lock())

    async def safe_call(cid, fn, *args, minimum_interval):
        lock = lock_for(cid)
        async with lock:
            now = time.monotonic()
            if now < cooldown_until.get(cid, 0.0):
                return False
            if now - last_edit.get(cid, 0.0) < minimum_interval:
                return False
            try:
                result = await fn(cid, *args)
                if result:
                    last_edit[cid] = time.monotonic()
                return bool(result)
            except Exception as exc:
                retry = getattr(exc, 'retry_after', None)
                try:
                    retry = max(1.0, float(retry)) if retry is not None else None
                except (TypeError, ValueError):
                    retry = None
                if retry is not None:
                    cooldown_until[cid] = time.monotonic() + retry + 2.0
                    app.log.warning('CANDICE TELEGRAM FLOOD COOLDOWN chat=%s retry_after=%.1fs — dropping disposable edits', cid, retry)
                    return False
                raise

    async def safe_edit_text(cid, mid, text, reply_markup=None):
        is_ready_timer = 'DECISION TIMER •' in str(text) and 'NEXT 5-MIN CHECKPOINT •' in str(text)
        interval = ready_interval if is_ready_timer else text_interval
        return await safe_call(cid, original_edit_text, mid, text, reply_markup, minimum_interval=interval)

    async def safe_edit_card(cid, mid, signal, remaining, phase):
        interval = card_pre_interval if phase == 'pre' else card_trade_interval
        return await safe_call(cid, original_edit_card, mid, signal, remaining, phase, minimum_interval=interval)

    app.edit_text = safe_edit_text
    app.edit_card = safe_edit_card
    app._candice_telegram_ratefix_v8 = True
    app.log.info('CANDICE TELEGRAM RATEFIX V8 ACTIVE — READY same-card=5s; text=30s; card-pre=5s; card-trade=15s; no queued edits')

from __future__ import annotations

import asyncio
import time


def install(app):
    """Serialize Telegram edits and throttle timer traffic by phase."""
    if getattr(app, '_candice_telegram_ratefix_v4', False):
        return

    original_edit_text = app.edit_text
    original_edit_card = app.edit_card
    locks = {}
    last_edit = {}
    cooldown_until = {}
    text_interval = 30.0
    card_trade_interval = 10.0
    card_pre_interval = 1.0

    def lock_for(chat_id):
        return locks.setdefault(chat_id, asyncio.Lock())

    async def wait_turn(chat_id, minimum_interval):
        async with lock_for(chat_id):
            now = time.monotonic()
            wait = max(0.0, minimum_interval - (now - last_edit.get(chat_id, 0.0)))
            cooldown = max(0.0, cooldown_until.get(chat_id, 0.0) - now)
            delay = max(wait, cooldown)
            if delay > 0:
                await asyncio.sleep(delay)
            last_edit[chat_id] = time.monotonic()

    def retry_seconds(exc):
        value = getattr(exc, 'retry_after', None)
        try:
            return max(1.0, float(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    async def safe_call(cid, fn, *args, minimum_interval):
        await wait_turn(cid, minimum_interval)
        try:
            return await fn(cid, *args)
        except Exception as exc:
            retry = retry_seconds(exc)
            if retry is not None:
                cooldown_until[cid] = max(cooldown_until.get(cid, 0.0), time.monotonic() + retry + 0.5)
                app.log.warning('CANDICE TELEGRAM FLOOD COOLDOWN chat=%s retry_after=%.1fs', cid, retry)
                return False
            raise

    async def safe_edit_text(cid, mid, text, reply_markup=None):
        return await safe_call(cid, original_edit_text, mid, text, reply_markup, minimum_interval=text_interval)

    async def safe_edit_card(cid, mid, signal, remaining, phase):
        interval = card_pre_interval if phase == 'pre' else card_trade_interval
        return await safe_call(cid, original_edit_card, mid, signal, remaining, phase, minimum_interval=interval)

    app.edit_text = safe_edit_text
    app.edit_card = safe_edit_card
    app._candice_telegram_ratefix_v4 = True
    app.log.info('CANDICE TELEGRAM RATEFIX V4 ACTIVE — text=30s trade-card=10s pre-entry=1s + RetryAfter')

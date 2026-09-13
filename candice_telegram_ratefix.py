from __future__ import annotations

import asyncio
import time


def install(app):
    """Serialize Telegram edits, throttle traffic, and honor flood-control cooldowns."""
    if getattr(app, '_candice_telegram_ratefix_v3', False):
        return

    original_edit_text = app.edit_text
    original_edit_card = app.edit_card
    locks = {}
    last_edit = {}
    cooldown_until = {}
    minimum_interval = 1.25

    def lock_for(chat_id):
        return locks.setdefault(chat_id, asyncio.Lock())

    async def wait_turn(chat_id):
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

    async def safe_call(cid, fn, *args):
        await wait_turn(cid)
        try:
            # IMPORTANT: cid belongs to the wrapped app method and must be
            # passed through. The previous V2 wrapper accidentally dropped it,
            # causing Telegram editMessageText to receive the text as message_id.
            return await fn(cid, *args)
        except Exception as exc:
            retry = retry_seconds(exc)
            if retry is not None:
                cooldown_until[cid] = max(cooldown_until.get(cid, 0.0), time.monotonic() + retry + 0.5)
                app.log.warning('CANDICE TELEGRAM FLOOD COOLDOWN chat=%s retry_after=%.1fs', cid, retry)
                return False
            raise

    async def safe_edit_text(cid, mid, text, reply_markup=None):
        return await safe_call(cid, original_edit_text, mid, text, reply_markup)

    async def safe_edit_card(cid, mid, signal, remaining, phase):
        return await safe_call(cid, original_edit_card, mid, signal, remaining, phase)

    app.edit_text = safe_edit_text
    app.edit_card = safe_edit_card
    app._candice_telegram_ratefix_v3 = True
    app.log.info('CANDICE TELEGRAM RATEFIX V3 ACTIVE — %.2fs minimum interval + RetryAfter cooldown', minimum_interval)

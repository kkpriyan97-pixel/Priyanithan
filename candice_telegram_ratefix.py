from __future__ import annotations

import asyncio
import time


def install(app):
    """Serialize Telegram edits and keep per-chat edit traffic below flood limits."""
    if getattr(app, '_candice_telegram_ratefix_v1', False):
        return

    original_edit_text = app.edit_text
    original_edit_card = app.edit_card
    locks = {}
    last_edit = {}
    minimum_interval = 1.25

    def lock_for(chat_id):
        return locks.setdefault(chat_id, asyncio.Lock())

    async def wait_turn(chat_id):
        async with lock_for(chat_id):
            now = time.monotonic()
            wait = minimum_interval - (now - last_edit.get(chat_id, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
            last_edit[chat_id] = time.monotonic()

    async def safe_edit_text(cid, mid, text, reply_markup=None):
        await wait_turn(cid)
        return await original_edit_text(cid, mid, text, reply_markup)

    async def safe_edit_card(cid, mid, signal, remaining, phase):
        await wait_turn(cid)
        return await original_edit_card(cid, mid, signal, remaining, phase)

    app.edit_text = safe_edit_text
    app.edit_card = safe_edit_card
    app._candice_telegram_ratefix_v1 = True
    app.log.info('CANDICE TELEGRAM RATEFIX ACTIVE — per-chat serialized edits — %.2fs minimum interval', minimum_interval)

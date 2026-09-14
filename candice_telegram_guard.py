from __future__ import annotations

import asyncio
import time


def install(app):
    """Final Telegram transport guard: serialize API traffic without queuing."""
    if getattr(app, '_candice_telegram_guard_v2', False):
        return
    tg = getattr(app, 'tg_app', None)
    bot = getattr(tg, 'bot', None) if tg is not None else None
    if bot is None:
        app.log.info('CANDICE TELEGRAM GUARD WAIT — bot not ready')
        return

    methods = ('send_message', 'send_photo', 'edit_message_text', 'edit_message_media')
    originals = {}
    for name in methods:
        fn = getattr(bot, name, None)
        if fn is not None:
            originals[name] = fn

    lock = asyncio.Lock()
    last_call = 0.0
    cooldown_until = 0.0
    min_interval = 2.0

    def retry_seconds(exc):
        value = getattr(exc, 'retry_after', None)
        try:
            return max(1.0, float(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    async def guarded(name, *args, **kwargs):
        nonlocal last_call, cooldown_until
        async with lock:
            now = time.monotonic()

            # Never sleep here. Sleeping while holding the global lock creates
            # a backlog when a 1s timer is active. Disposable edits are simply
            # dropped until the next eligible slot.
            if now < cooldown_until:
                return None
            if now - last_call < min_interval:
                return None

            try:
                result = await originals[name](*args, **kwargs)
                if result is not None:
                    last_call = time.monotonic()
                return result
            except Exception as exc:
                retry = retry_seconds(exc)
                if retry is not None:
                    cooldown_until = max(cooldown_until, time.monotonic() + retry + 1.0)
                    app.log.warning(
                        'CANDICE TELEGRAM GLOBAL COOLDOWN retry_after=%.1fs method=%s',
                        retry, name,
                    )
                    return None
                raise

    for name in originals:
        async def wrapper(*args, _name=name, **kwargs):
            return await guarded(_name, *args, **kwargs)
        setattr(bot, name, wrapper)

    app._candice_telegram_guard_v2 = True
    app.log.info('CANDICE TELEGRAM GUARD V2 ACTIVE — non-queued sends/edits + RetryAfter cooldown')
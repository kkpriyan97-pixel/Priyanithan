"""Telegram transport fallback for Render.

If the Render webhook cannot deliver updates, switch the bot to long polling and
feed updates into the same python-telegram-bot Application queue. This module
never executes broker trades and does not change trading logic.
"""
import asyncio
import sys
import threading
import time


_started = False


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


async def _poll(a):
    bot = a.telegram_application.bot
    offset = None
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        a.log.warning("TELEGRAM POLLING FALLBACK ACTIVE: webhook removed")
    except Exception as exc:
        a.log.warning("Telegram webhook delete failed: %s", exc)

    while True:
        try:
            updates = await bot.get_updates(
                offset=offset,
                timeout=20,
                allowed_updates=["message", "callback_query"],
            )
            for update in updates:
                offset = update.update_id + 1
                await a.telegram_application.update_queue.put(update)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            a.log.warning("Telegram polling error: %s", exc)
            await asyncio.sleep(3)


def _boot():
    global _started
    deadline = time.time() + 180
    while time.time() < deadline and not _started:
        try:
            a = _app()
            loop = getattr(a, "runtime_loop", None) if a else None
            application = getattr(a, "telegram_application", None) if a else None
            if a and loop and application:
                _started = True
                future = asyncio.run_coroutine_threadsafe(_poll(a), loop)
                a._telegram_polling_fallback_future = future
                a.log.warning("TELEGRAM POLLING FALLBACK STARTED")
                return
        except Exception:
            pass
        time.sleep(0.5)


threading.Thread(target=_boot, name="telegram-polling-fallback", daemon=True).start()

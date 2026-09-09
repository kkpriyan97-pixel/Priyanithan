"""Fast Manual Entry UI for Priyanithan.

Adds a Telegram inline button to approved signal messages. The button never
places a broker order; it only prepares the exact signal details for immediate
manual entry and preserves AUTO_TRADE=False.
"""
import asyncio
import re
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

_INSTALLED = False
_HANDLER_INSTALLED = False
_COMMANDS_PATCHED = False
_UAE = ZoneInfo("Asia/Dubai")
_SIGNAL_RE = re.compile(
    r"🔥?\s*PRIYANITHAN AI SIGNAL\s*🔥?.*?"
    r"📈\s*([^\n]+).*?"
    r"(⬆️\s*UP|⬇️\s*DOWN).*?"
    r"💰\s*Entry:\s*([^\n]+).*?"
    r"⏱️\s*Expiry:\s*(\d+)\s*MIN",
    re.S | re.I,
)


def _get_app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    return sys.modules.get("app")


def _button_for(text):
    """Return the manual-entry keyboard only for a valid approved signal."""
    m = _SIGNAL_RE.search(str(text or ""))
    if not m:
        return None
    pair = m.group(1).strip()
    direction = "UP" if "UP" in m.group(2).upper() else "DOWN"
    entry = m.group(3).strip()
    try:
        expiry = int(m.group(4))
    except (TypeError, ValueError):
        return None
    if not pair or not entry or expiry not in (1, 2, 3, 5, 10, 15):
        return None
    callback = f"FAST|{pair}|{direction}|{entry}|{expiry}"
    if len(callback.encode("utf-8")) > 64:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⚡ OPEN TRADE", callback_data=callback)]]
    )


async def _running_callback(update, context):
    query = update.callback_query
    if query is not None:
        await query.answer(
            "⏳ Trade is already running; the final market result will be sent automatically.",
            show_alert=False,
        )


async def _fast_manual_callback(update, context):
    query = update.callback_query
    if query is None:
        return
    await query.answer("⚡ Manual entry ready", show_alert=False)
    data = str(query.data or "")
    parts = data.split("|", 4)
    if len(parts) != 5 or parts[0] != "FAST":
        return
    _, pair, direction, entry, expiry = parts
    try:
        expiry_min = int(expiry)
    except (TypeError, ValueError):
        return
    if direction not in ("UP", "DOWN") or expiry_min not in (1, 2, 3, 5, 10, 15):
        return
    expiry_ts = time.time() + expiry_min * 60
    expiry_uae = datetime.fromtimestamp(expiry_ts, _UAE).strftime("%H:%M:%S UAE")
    text = (
        "⚡ FAST MANUAL ENTRY READY\n\n"
        f"📈 {pair}\n"
        f"{'⬆️' if direction == 'UP' else '⬇️'} {direction}\n"
        f"💰 Entry: {entry}\n"
        f"⏱️ Expiry: {expiry_min} MIN\n\n"
        "👤 Execute Buy/Sell manually in OlympTrade.\n"
        "🔒 AUTO TRADE: OFF\n"
        "🤖 Broker order automation: OFF\n\n"
        f"⏳ Target expiry: {expiry_uae}"
    )
    if query.message is not None:
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(
                        "⏳ TRADE RUNNING — MANUAL",
                        callback_data=f"RUNNING|{pair}|{direction}|{entry}|{expiry_min}",
                    )]]
                )
            )
        except Exception:
            pass
        await query.message.reply_text(text)


def _patch_command_handlers(application, appmod):
    """Replace already-registered /start and /scan callbacks after app import."""
    global _COMMANDS_PATCHED
    if _COMMANDS_PATCHED:
        return
    original_start = getattr(appmod, "start_cmd", None)
    original_scan = getattr(appmod, "scan_cmd", None)
    scan_cycle = getattr(appmod, "scan_cycle", None)
    if original_start is None or original_scan is None or scan_cycle is None:
        return

    async def patched_start(update, context):
        remember = getattr(appmod, "remember_chat", None)
        if callable(remember):
            remember(update)
        authorized = getattr(appmod, "is_authorized", lambda _u: False)(update)
        if not authorized:
            await update.message.reply_text(
                "Priyanithan AI is online. Use /access YOUR_CODE first; then /start again to begin a live scan."
            )
            return
        task = getattr(appmod, "manual_scan_task", None)
        if task is not None and not task.done():
            await update.message.reply_text("⏳ Live scan is already running. Please wait for the result.")
            return
        await update.message.reply_text("⚡ LIVE SCAN STARTED — analysing fresh market data now...")
        task = asyncio.create_task(scan_cycle(context.application), name="manual-scan")
        appmod.manual_scan_task = task

    async def patched_scan(update, context):
        remember = getattr(appmod, "remember_chat", None)
        if callable(remember):
            remember(update)
        authorized = getattr(appmod, "is_authorized", lambda _u: False)(update)
        if not authorized:
            await update.message.reply_text("❌ Not authorized. Use /access YOUR_CODE first.")
            return
        task = getattr(appmod, "manual_scan_task", None)
        if task is not None and not task.done():
            await update.message.reply_text("⏳ Live scan is already running. Please wait for the result.")
            return
        await update.message.reply_text("🔎 Live scan started — analysing fresh market data now...")
        task = asyncio.create_task(scan_cycle(context.application), name="manual-scan")
        appmod.manual_scan_task = task

    # PTB keeps handlers in application.handlers as groups of Handler objects.
    replaced = 0
    for handlers in getattr(application, "handlers", {}).values():
        for handler in handlers:
            callback = getattr(handler, "callback", None)
            command = getattr(handler, "commands", None)
            commands = {str(x).lower() for x in command} if command else set()
            if callback is original_start or "start" in commands:
                handler.callback = patched_start
                replaced += 1
            elif callback is original_scan or "scan" in commands:
                handler.callback = patched_scan
                replaced += 1
    if replaced:
        appmod.manual_scan_task = getattr(appmod, "manual_scan_task", None)
        _COMMANDS_PATCHED = True
        appmod.log.info("COMMAND HOTFIX ACTIVE: /start and /scan launch background live scans")


def _ensure_handler(application, appmod):
    global _HANDLER_INSTALLED
    if _HANDLER_INSTALLED or getattr(application, "_FAST_MANUAL_ENTRY_HANDLER", False):
        _HANDLER_INSTALLED = True
        return
    application.add_handler(CallbackQueryHandler(_fast_manual_callback, pattern=r"^FAST\|"))
    application.add_handler(CallbackQueryHandler(_running_callback, pattern=r"^RUNNING\|"))
    application._FAST_MANUAL_ENTRY_HANDLER = True
    _HANDLER_INSTALLED = True
    appmod.log.info("FAST MANUAL ENTRY CALLBACK ACTIVE: ⚡ OPEN TRADE -> ⏳ TRADE RUNNING")


def _wrap_send(appmod):
    current = getattr(appmod, "send_to_recipients", None)
    if current is None or getattr(current, "_FAST_MANUAL_WRAPPER", False):
        return False

    async def patched_send(bot, text):
        markup = _button_for(text)
        if markup is None:
            return await current(bot, text)
        ids = appmod.recipients()
        if not ids:
            return await current(bot, text)
        try:
            bot_id = int((await bot.get_me()).id)
        except Exception:
            bot_id = None
        sent = False
        for chat_id in ids:
            if bot_id is not None and int(chat_id) == bot_id:
                continue
            try:
                await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
                sent = True
            except Exception as exc:
                appmod.log.warning("Fast manual signal send failed chat_id=%s: %s", chat_id, exc)
        return sent

    patched_send._FAST_MANUAL_WRAPPER = True
    patched_send._FAST_MANUAL_INNER = current
    appmod.send_to_recipients = patched_send
    appmod.log.info("FAST MANUAL ENTRY SEND WRAPPER ACTIVE: signal buttons enabled")
    return True


def _install():
    global _INSTALLED
    appmod = _get_app()
    if appmod is None:
        return False
    application = getattr(appmod, "telegram_application", None)
    if application is None:
        return False
    _patch_command_handlers(application, appmod)
    _ensure_handler(application, appmod)
    changed = _wrap_send(appmod)
    if changed:
        _INSTALLED = True
    return True


def bootstrap():
    for _ in range(1800):
        try:
            _install()
        except Exception:
            appmod = _get_app()
            if appmod is not None and hasattr(appmod, "log"):
                appmod.log.exception("FAST MANUAL ENTRY BOOTSTRAP FAILED")
        time.sleep(1.0)


threading.Thread(target=bootstrap, name="fast-manual-entry-bootstrap", daemon=True).start()

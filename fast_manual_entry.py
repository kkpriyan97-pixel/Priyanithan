"""Fast Manual Entry UI for Priyanithan.

Adds a Telegram inline button to approved signal messages. The button never
places a broker order; it only prepares the exact signal details for immediate
manual entry and preserves AUTO_TRADE=False.
"""
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
_UAE = ZoneInfo("Asia/Dubai")
_SIGNAL_RE = re.compile(
    r"PRIYANITHAN AI SIGNAL.*?📈\s*([^\n]+).*?"
    r"(⬆️\s*UP|⬇️\s*DOWN).*?"
    r"💰\s*Entry:\s*([^\n]+).*?"
    r"⏱️\s*Expiry:\s*(\d+)\s*MIN",
    re.S,
)


def _get_app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    return sys.modules.get("app")


def _button_for(text):
    m = _SIGNAL_RE.search(str(text or ""))
    if not m:
        return None
    pair = m.group(1).strip()
    direction = "UP" if "UP" in m.group(2) else "DOWN"
    entry = m.group(3).strip()
    expiry = int(m.group(4))
    callback = f"FAST|{pair}|{direction}|{entry}|{expiry}"
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚡ OPEN TRADE", callback_data=callback)]])


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
    expiry_ts = time.time() + int(expiry) * 60
    expiry_uae = datetime.fromtimestamp(expiry_ts, _UAE).strftime("%H:%M:%S UAE")
    text = (
        "⚡ FAST MANUAL ENTRY READY\n\n"
        f"📈 {pair}\n"
        f"{'⬆️' if direction == 'UP' else '⬇️'} {direction}\n"
        f"💰 Entry: {entry}\n"
        f"⏱️ Expiry: {expiry} MIN\n\n"
        "👤 Execute Buy/Sell manually in OlympTrade.\n"
        "🔒 AUTO TRADE: OFF\n"
        "🤖 Broker order automation: OFF\n\n"
        f"⏳ Target expiry: {expiry_uae}"
    )
    await query.message.reply_text(text)


def _ensure_handler(application, appmod):
    global _HANDLER_INSTALLED
    if _HANDLER_INSTALLED or getattr(application, "_FAST_MANUAL_ENTRY_HANDLER", False):
        _HANDLER_INSTALLED = True
        return
    application.add_handler(CallbackQueryHandler(_fast_manual_callback, pattern=r"^FAST\|"))
    application._FAST_MANUAL_ENTRY_HANDLER = True
    _HANDLER_INSTALLED = True
    appmod.log.info("FAST MANUAL ENTRY CALLBACK ACTIVE: ⚡ OPEN TRADE")


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
    if appmod is None or getattr(appmod, "telegram_application", None) is None:
        return False
    application = appmod.telegram_application
    _ensure_handler(application, appmod)
    changed = _wrap_send(appmod)
    if changed:
        _INSTALLED = True
    return True


def bootstrap():
    # Keep checking because trade_result_monitor/single-signal wrappers can
    # replace send_to_recipients after startup. Re-wrap the latest sender so the
    # button cannot disappear after a restart or wrapper race.
    for _ in range(1800):
        try:
            _install()
        except Exception:
            appmod = _get_app()
            if appmod is not None and hasattr(appmod, "log"):
                appmod.log.exception("FAST MANUAL ENTRY BOOTSTRAP FAILED")
        time.sleep(1.0)


threading.Thread(target=bootstrap, name="fast-manual-entry-bootstrap", daemon=True).start()

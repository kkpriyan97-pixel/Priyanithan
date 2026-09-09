"""Fast Manual Entry UI for Priyanithan.

Adds a Telegram inline button to approved signal messages. The user must
choose DEMO or REAL before opening Olymptrade. The bot never places a broker
order and preserves AUTO_TRADE=False.
"""
import re
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

_INSTALLED = False
_HANDLER_INSTALLED = False
_BOT_SEND_PATCHED = False
_UAE = ZoneInfo("Asia/Dubai")
_OLYMPTRADE_URL = "https://olymptrade.com/pages/trading/"
_OLYMPTRADE_DEMO_URL = "https://olymptrade.com/pages/trading/account/free-demo/"
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


def _parse_signal(text):
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
    return pair, direction, entry, expiry


def _button_for(text):
    """Initial button: require explicit DEMO/REAL choice before redirect."""
    data = _parse_signal(text)
    if data is None:
        return None
    pair, direction, entry, expiry = data
    callback = f"MODE|{pair}|{direction}|{entry}|{expiry}"
    if len(callback.encode("utf-8")) > 64:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⚡ OPEN TRADE", callback_data=callback)]]
    )


def _mode_keyboard(pair, direction, entry, expiry):
    base = f"MODESEL|{pair}|{direction}|{entry}|{expiry}"
    demo = f"DEMO|{pair}|{direction}|{entry}|{expiry}"
    real = f"REAL|{pair}|{direction}|{entry}|{expiry}"
    if max(len(x.encode("utf-8")) for x in (demo, real)) > 64:
        return None
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧪 DEMO", callback_data=demo),
         InlineKeyboardButton("🔴 REAL", callback_data=real)]
    ])


async def _running_callback(update, context):
    query = update.callback_query
    if query is not None:
        await query.answer(
            "⏳ Trade is already running; final market result will be sent automatically.",
            show_alert=False,
        )


async def _mode_callback(update, context):
    query = update.callback_query
    if query is None:
        return
    await query.answer("Select DEMO or REAL", show_alert=False)
    parts = str(query.data or "").split("|", 4)
    if len(parts) != 5 or parts[0] != "MODE":
        return
    _, pair, direction, entry, expiry = parts
    keyboard = _mode_keyboard(pair, direction, entry, expiry)
    if keyboard is None or query.message is None:
        return
    await query.message.reply_text(
        "🎯 TRADE MODE SELECTION\n\n"
        f"📈 Asset: {pair}\n"
        f"{'⬆️' if direction == 'UP' else '⬇️'} Direction: {direction}\n"
        f"💰 Entry reference: {entry}\n"
        f"⏱️ Expiry: {expiry} MIN\n\n"
        "Choose the account mode before opening Olymptrade:",
        reply_markup=keyboard,
    )


async def _demo_callback(update, context):
    query = update.callback_query
    if query is None:
        return
    await query.answer("🧪 DEMO selected", show_alert=False)
    parts = str(query.data or "").split("|", 4)
    if len(parts) != 5:
        return
    _, pair, direction, entry, expiry = parts
    expiry_ts = time.time() + int(expiry) * 60
    expiry_uae = datetime.fromtimestamp(expiry_ts, _UAE).strftime("%H:%M:%S UAE")
    text = (
        "🧪 DEMO TRADE READY\n\n"
        f"📈 Asset: {pair}\n"
        f"{'⬆️' if direction == 'UP' else '⬇️'} Direction: {direction}\n"
        f"💰 Entry reference: {entry}\n"
        f"⏱️ Expiry: {expiry} MIN\n"
        f"⏳ Target expiry: {expiry_uae}\n\n"
        "⚠️ AUTO TRADE: OFF\n"
        "🤖 Broker order automation: OFF\n\n"
        "Open DEMO, then verify the exact asset and expiry on the platform "
        "before entering manually."
    )
    if query.message is not None:
        await query.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🧪 OPEN DEMO PLATFORM", url=_OLYMPTRADE_DEMO_URL)]
            ]),
        )


async def _real_callback(update, context):
    query = update.callback_query
    if query is None:
        return
    await query.answer("REAL mode selected — confirm before opening", show_alert=True)
    parts = str(query.data or "").split("|", 4)
    if len(parts) != 5 or query.message is None:
        return
    _, pair, direction, entry, expiry = parts
    await query.message.reply_text(
        "🔴 REAL ACCOUNT CONFIRMATION\n\n"
        f"📈 Asset: {pair}\n"
        f"{'⬆️' if direction == 'UP' else '⬇️'} Direction: {direction}\n"
        f"💰 Entry reference: {entry}\n"
        f"⏱️ Expiry: {expiry} MIN\n\n"
        "⚠️ REAL trading uses your own funds.\n"
        "The bot will NOT place the order. Verify the asset, account mode, "
        "amount and expiry yourself before any manual trade.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔴 CONFIRM & OPEN REAL", callback_data=f"REALCONF|{pair}|{direction}|{entry}|{expiry}")]
        ]),
    )


async def _real_confirm_callback(update, context):
    query = update.callback_query
    if query is None:
        return
    await query.answer("Opening official Olymptrade platform", show_alert=False)
    parts = str(query.data or "").split("|", 4)
    if len(parts) != 5 or query.message is None:
        return
    _, pair, direction, entry, expiry = parts
    await query.message.reply_text(
        "🔴 REAL MODE — MANUAL ONLY\n\n"
        f"Asset: {pair}\nDirection: {direction}\nEntry reference: {entry}\nExpiry: {expiry} MIN\n\n"
        "⚠️ Verify everything on Olymptrade before entering. AUTO TRADE: OFF.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔴 OPEN OLYMPTRADE", url=_OLYMPTRADE_URL)]
        ]),
    )


def _ensure_handler(application, appmod):
    global _HANDLER_INSTALLED
    if _HANDLER_INSTALLED or getattr(application, "_FAST_MANUAL_ENTRY_HANDLER", False):
        _HANDLER_INSTALLED = True
        return
    application.add_handler(CallbackQueryHandler(_mode_callback, pattern=r"^MODE\|"))
    application.add_handler(CallbackQueryHandler(_demo_callback, pattern=r"^DEMO\|"))
    application.add_handler(CallbackQueryHandler(_real_callback, pattern=r"^REAL\|"))
    application.add_handler(CallbackQueryHandler(_real_confirm_callback, pattern=r"^REALCONF\|"))
    application.add_handler(CallbackQueryHandler(_running_callback, pattern=r"^RUNNING\|"))
    application._FAST_MANUAL_ENTRY_HANDLER = True
    _HANDLER_INSTALLED = True
    appmod.log.info("FAST MANUAL ENTRY CALLBACK ACTIVE: DEMO/REAL selector enabled")


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
    appmod.log.info("FAST MANUAL ENTRY SEND WRAPPER ACTIVE: DEMO/REAL selector enabled")
    return True


def _patch_bot_send_message(appmod):
    """Final fail-safe: attach the DEMO/REAL selector to every approved signal."""
    global _BOT_SEND_PATCHED
    if _BOT_SEND_PATCHED:
        return False
    original = getattr(Bot, "send_message", None)
    if original is None or getattr(original, "_PRIYANITHAN_BUTTON_PATCH", False):
        _BOT_SEND_PATCHED = True
        return False

    async def patched_bot_send(self, *args, **kwargs):
        text = kwargs.get("text")
        if text is None and len(args) >= 2:
            text = args[1]
        markup = _button_for(text)
        if markup is not None and kwargs.get("reply_markup") is None:
            kwargs["reply_markup"] = markup
        return await original(self, *args, **kwargs)

    patched_bot_send._PRIYANITHAN_BUTTON_PATCH = True
    Bot.send_message = patched_bot_send
    _BOT_SEND_PATCHED = True
    appmod.log.info("TELEGRAM BOT-LAYER BUTTON PATCH ACTIVE: approved signals require DEMO/REAL choice")
    return True


def _install():
    global _INSTALLED
    appmod = _get_app()
    if appmod is None:
        return False
    _patch_bot_send_message(appmod)
    application = getattr(appmod, "telegram_application", None)
    if application is None:
        return True
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

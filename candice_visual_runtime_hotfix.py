"""Runtime wiring for Candice visual Telegram cards.

The important fix here is that Render runs app.py as __main__, so replacing
builtins.build_application is not enough: app.py calls its own module-local
build_application(). We therefore replace that exact function on the live app
module. This makes the visual callback deterministic for both SIGNAL and
RESEARCH asset screens.

No trading execution, martingale, forced signal, or AI-gate changes are made.
"""
from __future__ import annotations
import asyncio
import io
import importlib
import sys
import threading
import time

from telegram import InputMediaPhoto

PATCHED = False


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _card_module():
    return importlib.import_module("candice_visual_cards_hotfix")


def _fmt(v):
    n = max(0, int(v))
    h, r = divmod(n, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _asset(pair, state):
    c = _card_module()
    if state["active"]:
        return c._card(
            "session", "ASSET SELECTED", pair, pair,
            [
                "Fresh 1-minute candle verified",
                "Candice AI analysis ON",
                "Live market research continues 24/7",
                "AI duration = 2 / 3 / 5 / 10 / 15 MIN",
                f"Signal session ends: {state['end_uae']}",
                f"Next signal session: {state['next_signal_uae']}",
            ],
            _fmt(state["remaining_seconds"]),
        )
    return c._card(
        "research", "RESEARCH INTERVAL", pair, "", 
        [
            "Fresh 1-minute candle verified",
            "Candice AI research ON",
            "Live market research continues 24/7",
            "Signal generation is paused",
            f"Research ends: {state['end_uae']}",
            f"Next signal session: {state['next_signal_uae']}",
        ],
        _fmt(state["remaining_seconds"]),
    )


async def _send_image(bot, uid, image, caption=None):
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return await bot.send_photo(
        chat_id=uid,
        photo=buf,
        caption=caption or "Candice AI • Live Market • Manual Trade Only",
    )


async def _edit_image(bot, uid, message_id, image):
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    await bot.edit_message_media(
        chat_id=uid,
        message_id=message_id,
        media=InputMediaPhoto(media=buf),
    )


async def _visual_asset_callback(update, context):
    a = _app()
    q = getattr(update, "callback_query", None)
    if a is None or q is None:
        return
    data = q.data or ""

    # Pagination/refresh stays on the native handler.
    if not data.startswith("asset:"):
        original = getattr(a, "_CANDICE_NATIVE_ASSETS_CALLBACK", None)
        if original:
            await original(update, context)
        return

    await q.answer()
    uid = int(q.from_user.id)
    if uid not in getattr(a, "authorized_users", set()):
        await q.message.reply_text("Access required.")
        return

    pair = data.split(":", 1)[1].strip().upper()
    ok, err = await a.verify_live_pair(pair)
    if not ok:
        await q.message.reply_text(
            f"{pair} is not currently live: {err}\n\nChoose another live asset."
        )
        return

    a.selected_asset[uid] = pair
    a.active_signal.pop(uid, None)
    state = a.session_state()
    image = _asset(pair, state)
    msg = await _send_image(context.bot, uid, image)

    tasks = getattr(a, "_CANDICE_VISUAL_COUNTDOWN_TASKS", {})
    old = tasks.get(uid)
    if old and not old.done():
        old.cancel()
    task = asyncio.create_task(
        _countdown(context.bot, uid, int(msg.message_id), pair)
    )
    tasks[uid] = task
    a._CANDICE_VISUAL_COUNTDOWN_TASKS = tasks


async def _countdown(bot, uid, message_id, pair):
    a = _app()
    last = None
    for _ in range(5400):
        try:
            s = a.session_state()
            key = (s["active"], int(s["remaining_seconds"]))
            if key != last:
                await _edit_image(
                    bot, uid, message_id, _asset(pair, s)
                )
                last = key
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            try:
                a.log.warning("CANDICE VISUAL COUNTDOWN STOPPED: %s", exc)
            except Exception:
                pass
            return


async def _visual_session_broadcast(a, kind, event_ts, now_ts):
    bot = getattr(getattr(a, "telegram_application", None), "bot", None)
    if bot is None:
        return
    c = _card_module()
    remain = max(0, int(event_ts - now_ts))

    if kind == "START_PRE":
        card = c._card(
            "session", "SIGNAL SESSION STARTING", "Starts in", "",
            [
                "Candice research → signal preparation",
                "Selected assets remain active",
                "Next scan resumes at the next 5-minute boundary",
                "Manual trade only • Auto trade OFF",
            ],
            _fmt(remain),
        )
    elif kind == "START":
        card = c._card(
            "session", "SIGNAL SESSION STARTED", "SIGNAL WINDOW OPEN", "",
            [
                "Existing selected assets resume automatically",
                "Candice AI analysis ON",
                "Next eligible scan: next 5-minute boundary",
                "Manual trade only • Auto trade OFF",
            ],
        )
    elif kind == "END_PRE":
        card = c._card(
            "session", "SIGNAL SESSION ENDING", "Ends in", "",
            [
                "Candice is preparing research mode",
                "No new signal after session close",
                "Research continues 24/7",
            ],
            _fmt(remain),
        )
    else:
        card = c._card(
            "research", "SIGNAL SESSION ENDED", "RESEARCH ONLY • 1 HOUR", "",
            [
                "Market research continues 24/7",
                "Signal generation paused",
                "Next signal session will start automatically",
                "Manual trade only • Auto trade OFF",
            ],
        )

    recipients = set(getattr(a, "authorized_users", set()))
    raw = str(getattr(a, "TELEGRAM_CHAT_ID", "")).strip()
    if raw:
        try:
            recipients.add(int(raw))
        except Exception:
            pass
    for uid in sorted(int(x) for x in recipients):
        try:
            await _send_image(bot, uid, card)
        except Exception as exc:
            try:
                a.log.warning("CANDICE VISUAL SESSION SEND FAILED chat=%s: %s", uid, exc)
            except Exception:
                pass


def _patch_scheduler(a):
    try:
        mod = importlib.import_module("candice_session_automation_hotfix")
        if not getattr(mod, "_CANDICE_VISUAL_BROADCAST", False):
            mod._broadcast = _visual_session_broadcast
            mod._CANDICE_VISUAL_BROADCAST = True
    except Exception as exc:
        a.log.warning("VISUAL SESSION WIRING: %s", exc)


def _rebuild(a):
    def builder():
        application = (
            a.Application.builder()
            .token(a.TELEGRAM_BOT_TOKEN)
            .updater(None)
            .build()
        )
        application.add_handler(a.CommandHandler("start", a.start_cmd))
        application.add_handler(a.CommandHandler("access", a.access_cmd))
        application.add_handler(a.CommandHandler("assets", a.assets_cmd))
        application.add_handler(
            a.CommandHandler("session", getattr(a, "_session_cmd", a.access_cmd))
        )
        application.add_handler(
            a.CallbackQueryHandler(
                a._CANDICE_VISUAL_ASSET_CALLBACK,
                pattern=r"^(asset:|assets:)",
            )
        )
        a._CANDICE_SESSION_HANDLER_REGISTERED = True
        a.log.warning("CANDICE VISUAL BUILDER ACTIVE: image-first Telegram UI")
        return application

    builder._CANDICE_VISUAL_BUILDER = True
    # Critical: app.py calls its own module-local build_application().
    # Replace that exact function, not only builtins.build_application.
    a.build_application = builder


def _patch():
    global PATCHED
    a = _app()
    if a is None or not callable(getattr(a, "verify_live_pair", None)):
        return False
    if getattr(a, "_CANDICE_VISUAL_RUNTIME", False):
        PATCHED = True
        return True

    # Preserve native callback for pagination/refresh.
    if callable(getattr(a, "assets_callback", None)):
        a._CANDICE_NATIVE_ASSETS_CALLBACK = a.assets_callback

    a._CANDICE_VISUAL_ASSET_CALLBACK = _visual_asset_callback
    a._CANDICE_VISUAL_RUNTIME = True
    _patch_scheduler(a)
    _rebuild(a)
    a.log.warning(
        "CANDICE VISUAL RUNTIME ACTIVE: 3D-style asset/session cards + live image countdown"
    )
    PATCHED = True
    return True


def _boot():
    for _ in range(1800):
        try:
            if _patch():
                return
        except Exception:
            pass
        time.sleep(0.2)


threading.Thread(
    target=_boot,
    name="candice-visual-runtime",
    daemon=True,
).start()

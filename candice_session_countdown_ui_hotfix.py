"""Live countdown for selected-asset session UI.

Adds a continuously updating Telegram countdown to the selected-asset message
while preserving the canonical 2h signal / 1h research clock. Manual trading
only: no broker order execution, no forced signals, no martingale.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import threading
import time

PATCHED = False


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _clock():
    a = _app()
    if a is not None and callable(getattr(a, "session_state", None)):
        return a.session_state()
    return None


def _fmt(seconds: float) -> str:
    n = max(0, int(seconds))
    h, r = divmod(n, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _text(pair: str, s: dict) -> str:
    a = _app()
    if s["active"]:
        return (
            f"✅ ASSET SELECTED — {pair}\n\n"
            "🟢 Fresh 1-minute candle verified\n"
            "🤖 Candice AI analysis ON\n"
            "🚀 SIGNAL SESSION ACTIVE\n\n"
            f"⏳ SIGNAL COUNTDOWN: {_fmt(s['remaining_seconds'])}\n"
            f"🏁 Signal session ends: {s['end_uae']}\n"
            f"🚀 Next signal session: {s['next_signal_uae']}\n\n"
            "⏱️ AI duration = 2 / 3 / 5 / 10 / 15 MIN\n"
            "⚠️ MANUAL TRADE ONLY — AUTO TRADE OFF"
        )
    return (
        f"🧠 {pair} — RESEARCH INTERVAL\n\n"
        "🟢 Fresh 1-minute candle verified\n"
        "🤖 Candice AI research ON\n"
        "📡 Live market research continues 24/7\n"
        "🚫 Signal generation is paused during this interval.\n\n"
        f"⏳ RESEARCH COUNTDOWN: {_fmt(s['remaining_seconds'])}\n"
        f"🏁 Research ends: {s['end_uae']}\n"
        f"🚀 Next signal session: {s['next_signal_uae']}\n\n"
        "⚠️ No forced signal • Manual trade only"
    )


async def _countdown(bot, chat_id: int, message_id: int, pair: str):
    """Edit the selected-asset message every ~2s using the canonical clock."""
    last_text = None
    for _ in range(5400):  # hard safety cap; exits naturally at session boundary
        try:
            s = _clock()
            if not s:
                return
            text = _text(pair, s)
            if text != last_text:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
                last_text = text
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            return
        except Exception:
            # Telegram may reject an edit after another handler replaces the
            # message. Stop rather than fighting the newer UI.
            return


def _install_wrapper():
    global PATCHED
    mod = importlib.import_module("candice_runtime_final_hotfix")
    if getattr(mod, "_CANDICE_COUNTDOWN_WRAPPED", False):
        PATCHED = True
        return True

    original = mod._asset_callback

    async def countdown_asset_callback(update, context):
        # Let the canonical handler validate access/live asset and send the
        # initial message first. Then attach a single countdown editor.
        before = None
        q = getattr(update, "callback_query", None)
        if q is not None:
            before = getattr(q, "message", None)

        await original(update, context)

        a = _app()
        q = getattr(update, "callback_query", None)
        msg = getattr(q, "message", None) if q is not None else before
        if a is None or q is None or msg is None:
            return
        data = q.data or ""
        if not data.startswith("asset:"):
            return
        uid = int(q.from_user.id)
        pair = data.split(":", 1)[1].strip().upper()
        if a.selected_asset.get(uid) != pair:
            return
        # One countdown per selected-asset message.
        key = (uid, int(msg.message_id), pair)
        tasks = getattr(a, "_CANDICE_SESSION_COUNTDOWN_TASKS", {})
        old = tasks.get(uid)
        if old and not old.done():
            old.cancel()
        task = asyncio.create_task(_countdown(context.bot, int(msg.chat_id), int(msg.message_id), pair))
        tasks[uid] = task
        a._CANDICE_SESSION_COUNTDOWN_TASKS = tasks

    mod._asset_callback = countdown_asset_callback
    mod._CANDICE_COUNTDOWN_WRAPPED = True
    # Rebuild the final builder so its CallbackQueryHandler captures the new
    # wrapper. The next build is the only registration used by app.py.
    import builtins
    builder = getattr(builtins, "build_application", None)
    if callable(builder):
        old_builder = builder
        def countdown_build_application():
            app = _app()
            application = app.Application.builder().token(app.TELEGRAM_BOT_TOKEN).updater(None).build()
            application.add_handler(app.CommandHandler("start", app.start_cmd))
            application.add_handler(app.CommandHandler("access", app.access_cmd))
            application.add_handler(app.CommandHandler("assets", app.assets_cmd))
            application.add_handler(app.CommandHandler("session", mod._session_cmd))
            application.add_handler(app.CallbackQueryHandler(mod._asset_callback, pattern=r"^(asset:|assets:)"))
            app._CANDICE_SESSION_HANDLER_REGISTERED = True
            app.log.warning("CANDICE SESSION COUNTDOWN: live 2s asset countdown active")
            return application
        countdown_build_application._CANDICE_COUNTDOWN_BUILDER = True
        builtins.build_application = countdown_build_application
    a = _app()
    if a is not None:
        a.log.warning("CANDICE SESSION COUNTDOWN UI ACTIVE: live countdown every 2s")
    PATCHED = True
    return True


def _boot():
    for _ in range(1800):
        try:
            a = _app()
            if a is not None and getattr(a, "_CANDICE_FINAL_SESSION_CLOCK", False):
                if _install_wrapper():
                    return
        except Exception:
            pass
        time.sleep(0.2)

threading.Thread(target=_boot, name="candice-session-countdown", daemon=True).start()

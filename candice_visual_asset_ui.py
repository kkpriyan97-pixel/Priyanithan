"""Forced Candice visual asset-selection callback.

The asset button must always resolve to the animated Telegram UI. This module
also replaces the canonical app callback reference so no later registration
can accidentally capture the old text-only callback.
"""
from __future__ import annotations

import importlib
import sys
import threading
import time

CAPTION = "Candice AI • Live Market • Manual Trade Only"


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


async def _callback(update, context):
    a = _app()
    q = getattr(update, "callback_query", None)
    if a is None or q is None:
        return
    data = (q.data or "").strip()
    if not data.startswith("asset:"):
        native = getattr(a, "_native_assets_callback", None)
        if native:
            await native(update, context)
        return

    try:
        await q.answer()
    except Exception:
        pass

    uid = int(q.from_user.id)
    if uid not in getattr(a, "authorized_users", set()):
        await q.message.reply_text("❌ Access required.")
        return

    pair = data.split(":", 1)[1].strip().upper()
    ok, err = await a.verify_live_pair(pair)
    if not ok:
        await q.message.reply_text(
            f"⚠️ {pair} is not currently live: {err}\n\nChoose another live asset."
        )
        return

    a.selected_asset[uid] = pair
    a.active_signal.pop(uid, None)

    cards = importlib.import_module("candice_visual_cards_hotfix")
    image = cards._card(
        "session",
        "ASSET SELECTED",
        pair,
        pair,
        [
            "Fresh 1-minute candle verified",
            "Candice AI analysis ON",
            "Live market research continues 24/7",
            "AI duration = 2 / 3 / 5 / 10 / 15 MIN",
            "Manual trade only • Auto trade OFF",
        ],
    )
    buf = cards.animated_card_bytes(image=image)
    if buf is None:
        await q.message.reply_text("⚠️ Visual animation could not be generated.")
        return

    # Remove the old inline-keyboard message after the callback is accepted.
    # The new message is intentionally a Telegram animation, not text/photo.
    try:
        await q.message.delete()
    except Exception:
        pass

    try:
        await context.bot.send_animation(
            chat_id=uid,
            animation=buf,
            caption=CAPTION,
            width=image.width,
            height=image.height,
        )
    except Exception as exc:
        a.log.exception("CANDICE ASSET ANIMATION SEND FAILED: %s", exc)
        # Keep a visible diagnostic instead of silently falling back to the
        # old text-only asset selection UI.
        await context.bot.send_message(
            chat_id=uid,
            text="⚠️ Candice visual card failed to send. Check Render logs for CANDICE ASSET ANIMATION SEND FAILED.",
        )


def force_register(application, a):
    """Make the visual callback the only asset callback in this application."""
    native = getattr(a, "assets_callback", None)
    if native is not _callback:
        if native is not None and getattr(native, "__name__", "") != "_callback":
            a._native_assets_callback = native
        a.assets_callback = _callback

    # Remove any handler whose callback is the old app.assets_callback and
    # re-add one deterministic visual handler in group 0.
    try:
        for group, handlers in list(application.handlers.items()):
            kept = []
            for h in handlers:
                cb = getattr(h, "callback", None)
                pattern = getattr(h, "pattern", None)
                is_asset_pattern = bool(pattern and str(pattern).startswith("^(asset:"))
                is_old_callback = getattr(cb, "__name__", "") == "assets_callback"
                if is_asset_pattern or is_old_callback:
                    continue
                kept.append(h)
            application.handlers[group] = kept
    except Exception as exc:
        a.log.warning("CANDICE ASSET HANDLER CLEANUP WARNING: %s", exc)

    application.add_handler(
        a.CallbackQueryHandler(_callback, pattern=r"^(asset:|assets:)"),
        group=0,
    )
    a.log.warning("CANDICE VISUAL ASSET UI FORCED: animation callback is canonical")
    return application


def _install():
    a = _app()
    if a is None:
        return False
    if not callable(getattr(a, "assets_callback", None)):
        return False
    if getattr(a, "_CANDICE_VISUAL_ASSET_UI", False):
        return True
    a._native_assets_callback = a.assets_callback
    a.assets_callback = _callback
    a._CANDICE_VISUAL_ASSET_UI = True
    a.log.warning("CANDICE VISUAL ASSET UI ACTIVE: forced animated callback")
    return True


def _boot():
    for _ in range(900):
        try:
            if _install():
                return
        except Exception:
            pass
        time.sleep(0.2)


threading.Thread(target=_boot, name="candice-visual-asset-ui", daemon=True).start()

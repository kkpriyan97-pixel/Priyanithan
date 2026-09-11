"""Canonical visual asset-selection UI."""
import importlib
import sys
import threading
import time


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


async def _callback(update, context):
    a = _app(); q = getattr(update, "callback_query", None)
    if a is None or q is None: return
    data = q.data or ""
    if not data.startswith("asset:"):
        native = getattr(a, "_native_assets_callback", None)
        if native: await native(update, context)
        return
    await q.answer()
    uid = int(q.from_user.id)
    if uid not in getattr(a, "authorized_users", set()):
        await q.message.reply_text("❌ Access required."); return
    pair = data.split(":", 1)[1].strip().upper()
    ok, err = await a.verify_live_pair(pair)
    if not ok:
        await q.message.reply_text(f"⚠️ {pair} is not currently live: {err}\n\nChoose another live asset."); return
    a.selected_asset[uid] = pair
    a.active_signal.pop(uid, None)
    cards = importlib.import_module("candice_visual_cards_hotfix")
    image = cards._card("session", "ASSET SELECTED", pair, pair, [
        "Fresh 1-minute candle verified", "Candice AI analysis ON",
        "Live market research continues 24/7",
        "AI duration = 2 / 3 / 5 / 10 / 15 MIN",
        "Manual trade only • Auto trade OFF"])
    buf = cards.io.BytesIO(); image.save(buf, format="PNG", optimize=True); buf.seek(0)
    await context.bot.send_photo(chat_id=uid, photo=buf, caption="Candice AI • Live Market • Manual Trade Only")


def _install():
    a = _app()
    if a is None or not callable(getattr(a, "assets_callback", None)): return False
    if getattr(a, "_CANDICE_VISUAL_ASSET_UI", False): return True
    a._native_assets_callback = a.assets_callback
    a.assets_callback = _callback
    a._CANDICE_VISUAL_ASSET_UI = True
    a.log.warning("CANDICE VISUAL ASSET UI ACTIVE: image-first asset selection")
    return True


def _boot():
    for _ in range(900):
        try:
            if _install(): return
        except Exception: pass
        time.sleep(0.2)

threading.Thread(target=_boot, name="candice-visual-asset-ui", daemon=True).start()

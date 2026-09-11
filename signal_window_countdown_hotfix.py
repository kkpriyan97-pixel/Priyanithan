"""Live next-signal-window countdown for selected assets.

After an asset is selected, Telegram shows a live countdown to the next
5-minute scan boundary. The existing scan_loop remains the authority that
actually starts analysis, so the UI cannot create a fake signal. No broker
order execution is enabled.
"""
import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

PATCHED = False
UPDATE_SECONDS = 1


def _app():
    import sys
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _next_boundary(ts=None):
    if ts is None:
        ts = time.time()
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    minute = ((dt.minute // 5) + 1) * 5
    if minute >= 60:
        target = (dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    else:
        target = dt.replace(minute=minute, second=0, microsecond=0)
    return target.timestamp()


def _clock(seconds):
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    return f"00:{m:02d}:{s:02d}"


async def _window_countdown(a, bot, uid, pair):
    try:
        msg = await bot.send_message(
            chat_id=uid,
            text=(
                f"🎯 {pair} — SIGNAL WINDOW ARMED\n\n"
                "🤖 Candice AI: LIVE\n"
                "🕯️ Waiting for the next closed 1-minute candle\n\n"
                "⏳ NEXT SIGNAL GENERATION\n"
                "🔢 00:00:00\n\n"
                "⚡ At 00:00 → AI analysis starts\n"
                "⚠️ No forced signal • Manual trade only"
            ),
        )
    except Exception as exc:
        a.log.debug("SIGNAL WINDOW COUNTDOWN initial send failed: %s", exc)
        return
    last = None
    while a.selected_asset.get(uid) == pair and uid not in a.active_signal:
        boundary = _next_boundary()
        remaining = boundary - time.time()
        if remaining <= 0:
            try:
                await bot.edit_message_text(
                    chat_id=uid,
                    message_id=msg.message_id,
                    text=(
                        f"🚀 {pair} — SIGNAL WINDOW OPEN\n\n"
                        "🤖 Candice AI is analyzing the fresh candle now.\n"
                        "📊 Checking 1m trigger + 5m context + AI gate…\n\n"
                        "⏳ ANALYSIS RUNNING\n"
                        "⚠️ No forced signal • Manual trade only"
                    ),
                )
            except Exception:
                pass
            return
        whole = int(remaining)
        text = (
            f"🎯 {pair} — SIGNAL WINDOW ARMED\n\n"
            "🤖 Candice AI: LIVE\n"
            "🕯️ Fresh 1-minute candle scan active\n\n"
            "⏳ NEXT SIGNAL GENERATION\n"
            f"🔢 {_clock(whole)}\n"
            f"🏁 Window: {a.fmt_ts(boundary)}\n\n"
            "⚡ At 00:00 → AI analysis starts\n"
            "⚠️ No forced signal • Manual trade only"
        )
        if text != last:
            try:
                await bot.edit_message_text(chat_id=uid, message_id=msg.message_id, text=text)
                last = text
            except Exception as exc:
                a.log.debug("SIGNAL WINDOW COUNTDOWN edit skipped: %s", exc)
        await asyncio.sleep(UPDATE_SECONDS)


async def _patched_assets_callback(update, context):
    a = _app()
    query = update.callback_query
    if not query or not a:
        return
    data = query.data or ""
    if not data.startswith("asset:"):
        return await _ORIGINAL(update, context)
    await query.answer()
    uid = int(query.from_user.id)
    if uid not in a.authorized_users:
        await query.message.reply_text("❌ Access required.")
        return
    pair = data.split(":", 1)[1].strip().upper()
    ok, err = await a.verify_live_pair(pair)
    if not ok:
        await query.message.reply_text(f"⚠️ {pair} is not currently live: {err}\n\nChoose another live asset.")
        return
    a.selected_asset[uid] = pair
    a.active_signal.pop(uid, None)
    await query.message.reply_text(
        f"✅ ASSET SELECTED — {pair}\n\n"
        "🟢 Fresh 1-minute candle verified\n"
        "🤖 Candice AI analysis ON\n"
        "⏱️ Next signal window = next 5 minutes\n"
        "⏱️ AI duration = 2 / 3 / 5 / 10 / 15 MIN\n"
        "⚠️ MANUAL TRADE ONLY — AUTO TRADE OFF"
    )
    asyncio.create_task(_window_countdown(a, context.bot, uid, pair), name=f"signal-window-{uid}-{pair}")


_ORIGINAL = None


def _patch():
    global PATCHED, _ORIGINAL
    a = _app()
    if not a:
        return False
    if getattr(a, "_SIGNAL_WINDOW_COUNTDOWN_V1", False):
        PATCHED = True
        return True
    original = getattr(a, "assets_callback", None)
    if not callable(original):
        return False
    _ORIGINAL = original
    a.assets_callback = _patched_assets_callback
    a._SIGNAL_WINDOW_COUNTDOWN_V1 = True
    a.log.warning("SIGNAL WINDOW COUNTDOWN V1 ACTIVE: live countdown to next 5m scan boundary")
    PATCHED = True
    return True


def _boot():
    for _ in range(1800):
        try:
            if _patch():
                return
        except Exception:
            pass
        time.sleep(0.1)

threading.Thread(target=_boot, name="signal-window-countdown", daemon=True).start()

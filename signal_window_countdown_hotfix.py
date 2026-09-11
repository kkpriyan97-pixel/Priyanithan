"""Live selected-asset signal-window countdown with boundary catch-up.

If an asset is selected just after a 5-minute boundary, the bot must not
silently skip that just-closed candle and move the user to the next boundary.
Within a short post-boundary grace period, trigger exactly one immediate
selected-asset scan, then return to the normal 5-minute schedule.

No broker order execution is enabled.
"""
import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

PATCHED = False
UPDATE_SECONDS = 1
BOUNDARY_GRACE_SECONDS = 45


def _app():
    import sys
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _floor_boundary(ts=None):
    if ts is None:
        ts = time.time()
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    minute = (dt.minute // 5) * 5
    return dt.replace(minute=minute, second=0, microsecond=0).timestamp()


def _next_boundary(ts=None):
    if ts is None:
        ts = time.time()
    current = _floor_boundary(ts)
    elapsed = ts - current
    # During the first 45 seconds after a boundary, that boundary is the
    # active generation window. This prevents 15:20 from being skipped and
    # incorrectly displayed as 15:25 when the asset is selected at 15:20:xx.
    if 0 <= elapsed <= BOUNDARY_GRACE_SECONDS:
        return current
    return current + 300.0


def _clock(seconds):
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    return f"00:{m:02d}:{s:02d}"


async def _kick_current_boundary_scan(a, uid, pair, bot):
    now = time.time()
    boundary = _floor_boundary(now)
    elapsed = now - boundary
    if elapsed < 0 or elapsed > BOUNDARY_GRACE_SECONDS:
        return
    key = (int(uid), pair, int(boundary))
    kicked = getattr(a, "_SIGNAL_WINDOW_KICKED", None)
    if kicked is None:
        kicked = set()
        a._SIGNAL_WINDOW_KICKED = kicked
    if key in kicked:
        return
    kicked.add(key)
    scan = getattr(a, "scan_cycle", None)
    application = getattr(a, "telegram_application", None)
    if not callable(scan) or application is None:
        a.log.warning("SIGNAL WINDOW IMMEDIATE SCAN unavailable: scan_cycle/application not ready")
        return
    try:
        a.log.info("SIGNAL WINDOW IMMEDIATE SCAN: pair=%s uid=%s boundary=%s", pair, uid, a.fmt_ts(boundary))
        await scan(application)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        a.log.exception("SIGNAL WINDOW IMMEDIATE SCAN FAILED: pair=%s uid=%s: %s", pair, uid, exc)


async def _window_countdown(a, bot, uid, pair):
    try:
        msg = await bot.send_message(
            chat_id=uid,
            text=(
                f"🎯 {pair} — SIGNAL WINDOW ARMED\n\n"
                "🤖 Candice AI: LIVE\n"
                "🕯️ Fresh 1-minute candle scan active\n\n"
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
        now = time.time()
        boundary = _next_boundary(now)
        remaining = boundary - now
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
            # Do not wait for the next 5-minute cycle here. The selected-asset
            # scan is kicked exactly once at this boundary by the callback.
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
        "⏱️ Signal generation = current 5-minute window when available\n"
        "⏱️ AI duration = 2 / 3 / 5 / 10 / 15 MIN\n"
        "⚠️ MANUAL TRADE ONLY — AUTO TRADE OFF"
    )
    # If the user selects the asset immediately after 15:20/15:25/etc.,
    # analyze the just-closed candle now instead of silently waiting for 15:25.
    asyncio.create_task(
        _kick_current_boundary_scan(a, uid, pair, context.bot),
        name=f"signal-window-kick-{uid}-{pair}",
    )
    asyncio.create_task(
        _window_countdown(a, context.bot, uid, pair),
        name=f"signal-window-{uid}-{pair}",
    )


_ORIGINAL = None


def _patch():
    global PATCHED, _ORIGINAL
    a = _app()
    if not a:
        return False
    if getattr(a, "_SIGNAL_WINDOW_COUNTDOWN_V2", False):
        PATCHED = True
        return True
    original = getattr(a, "assets_callback", None)
    if not callable(original):
        return False
    _ORIGINAL = original
    a.assets_callback = _patched_assets_callback
    a._SIGNAL_WINDOW_COUNTDOWN_V2 = True
    a.log.warning("SIGNAL WINDOW COUNTDOWN V2 ACTIVE: current-boundary catch-up + next 5m cycle")
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

threading.Thread(target=_boot, name="signal-window-countdown-v2", daemon=True).start()

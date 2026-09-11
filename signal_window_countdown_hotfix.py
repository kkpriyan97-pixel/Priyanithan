"""Live selected-asset signal-window countdown with reliable boundary scan.

The countdown is UI only; the actual selected-asset scan is explicitly
triggered at the current/new 5-minute boundary.  No broker order execution.
"""
import asyncio
import threading
import time
from datetime import datetime, timezone

PATCHED = False
UPDATE_SECONDS = 1
BOUNDARY_GRACE_SECONDS = 45
SCAN_READY_RETRY_SECONDS = 0.25
SCAN_READY_TIMEOUT_SECONDS = 15


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
    if 0 <= elapsed <= BOUNDARY_GRACE_SECONDS:
        return current
    return current + 300.0


def _clock(seconds):
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    return f"00:{m:02d}:{s:02d}"


async def _run_boundary_scan(a, uid, pair, bot, boundary):
    """Run exactly one selected-asset scan for this 5-minute boundary.

    Hotfix startup and Telegram webhook startup are asynchronous, so the old
    implementation could reach the boundary before scan_cycle/application was
    installed and then permanently skip the scan.  Wait briefly for the
    already-starting native engine instead of dropping the window.
    """
    key = (int(uid), str(pair).upper(), int(boundary))
    kicked = getattr(a, "_SIGNAL_WINDOW_KICKED", None)
    if kicked is None:
        kicked = set()
        a._SIGNAL_WINDOW_KICKED = kicked
    if key in kicked:
        return False

    deadline = time.monotonic() + SCAN_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        scan = getattr(a, "scan_cycle", None)
        application = getattr(a, "telegram_application", None)
        if callable(scan) and application is not None:
            kicked.add(key)
            try:
                a.log.info(
                    "SIGNAL WINDOW SCAN TRIGGERED: pair=%s uid=%s boundary=%s",
                    pair, uid, a.fmt_ts(boundary),
                )
                await scan(application)
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                a.log.exception(
                    "SIGNAL WINDOW SCAN FAILED: pair=%s uid=%s boundary=%s: %s",
                    pair, uid, a.fmt_ts(boundary), exc,
                )
                return False
        await asyncio.sleep(SCAN_READY_RETRY_SECONDS)

    a.log.error(
        "SIGNAL WINDOW SCAN UNAVAILABLE AFTER RETRY: pair=%s uid=%s boundary=%s",
        pair, uid, a.fmt_ts(boundary),
    )
    return False


async def _kick_current_boundary_scan(a, uid, pair, bot):
    now = time.time()
    boundary = _floor_boundary(now)
    elapsed = now - boundary
    if elapsed < 0 or elapsed > BOUNDARY_GRACE_SECONDS:
        return
    await _run_boundary_scan(a, uid, pair, bot, boundary)


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
            # Critical: the countdown itself owns the boundary trigger.  This
            # guarantees future 5-minute windows are not skipped even if the
            # selection-time catch-up task was not needed or already finished.
            await _run_boundary_scan(a, uid, pair, bot, boundary)
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
                await bot.edit_message_text(
                    chat_id=uid, message_id=msg.message_id, text=text
                )
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
        await query.message.reply_text(
            f"⚠️ {pair} is not currently live: {err}\n\nChoose another live asset."
        )
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
    # Catch the just-opened boundary immediately (15:20, 15:25, ...). The
    # helper waits briefly if the final scan engine is still booting.
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
    if getattr(a, "_SIGNAL_WINDOW_COUNTDOWN_V3", False):
        PATCHED = True
        return True
    original = getattr(a, "assets_callback", None)
    if not callable(original):
        return False
    _ORIGINAL = original
    a.assets_callback = _patched_assets_callback
    a._SIGNAL_WINDOW_COUNTDOWN_V3 = True
    a.log.warning(
        "SIGNAL WINDOW COUNTDOWN V3 ACTIVE: boundary trigger + engine-ready retry"
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
        time.sleep(0.1)


threading.Thread(
    target=_boot, name="signal-window-countdown-v3", daemon=True
).start()

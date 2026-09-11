"""Digital Telegram countdown for approved manual signals.

Edits the already-sent signal message in place so the user sees a live
HH:MM:SS countdown until the exact signal expiry. The countdown runs as a
background asyncio task, so the existing result monitor starts immediately.
No broker order is created or modified.
"""
import asyncio
import sys
import threading
import time


UPDATE_SECONDS = 2
PATCHED = False


def _app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    return sys.modules.get("app")


def _clock(seconds):
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _bar(progress, width=12):
    progress = max(0, min(100, int(progress)))
    filled = round(width * progress / 100)
    return "█" * filled + "░" * (width - filled)


async def _countdown_signal(module, bot, uid, signal):
    created = float(signal.get("created_at", time.time()))
    duration = int(signal.get("duration", 5))
    expiry_ts = created + duration * 60
    pair = str(signal.get("pair", "ASSET"))
    direction = str(signal.get("direction", "UP")).upper()
    arrow = "⬆️ UP" if direction == "UP" else "⬇️ DOWN"
    entry = module.fmt_price(signal.get("price", "LIVE"))
    ai_conf = int(signal.get("ai_confidence", 0) or 0)
    tech_conf = int(signal.get("confidence", 0) or 0)
    trend = str(signal.get("trend_5m", "UNKNOWN"))
    candle = str(signal.get("candle_time", module.fmt_ts(created)))
    reason = str(signal.get("ai_reason", "Candice AI approved live structure"))[:220]

    try:
        message = await bot.send_message(
            chat_id=uid,
            text=(
                "🔥 PRIYANITHAN AI SIGNAL 🔥\n\n"
                f"📈 {pair}\n"
                f"{arrow}\n"
                f"💰 Entry: {entry}\n"
                f"⏱️ Duration: {duration} MIN\n"
                f"🤖 Candice AI: APPROVED ({ai_conf}%)\n"
                f"📊 Technical: {tech_conf}%\n"
                f"🕯️ 5m Trend: {trend}\n"
                f"🕐 Candle: {candle}\n"
                f"🧠 {reason}\n\n"
                "⏳ TIME REMAINING\n"
                f"🔢 {_clock(duration * 60)}\n"
                f"[{_bar(100)}] 100%\n\n"
                "⚠️ MANUAL TRADE — AUTO TRADE OFF"
            ),
        )
    except Exception:
        module.log.exception("DIGITAL COUNTDOWN: initial Telegram send failed")
        return

    total = max(1, duration * 60)
    last_text = None
    while True:
        remaining = expiry_ts - time.time()
        if remaining <= 0:
            break
        whole = int(remaining)
        progress = int((remaining / total) * 100)
        urgency = "🚨" if whole <= 10 else "⏳"
        text = (
            "🔥 PRIYANITHAN AI SIGNAL 🔥\n\n"
            f"📈 {pair}\n"
            f"{arrow}\n"
            f"💰 Entry: {entry}\n"
            f"⏱️ Duration: {duration} MIN\n"
            f"🤖 Candice AI: APPROVED ({ai_conf}%)\n"
            f"📊 Technical: {tech_conf}%\n"
            f"🕯️ 5m Trend: {trend}\n"
            f"🕐 Candle: {candle}\n"
            f"🧠 {reason}\n\n"
            f"{urgency} TIME REMAINING\n"
            f"🔢 {_clock(whole)}\n"
            f"[{_bar(progress)}] {progress}%\n"
            f"🏁 Expiry: {module.fmt_ts(expiry_ts)}\n\n"
            "⚠️ MANUAL TRADE — AUTO TRADE OFF"
        )
        if text != last_text:
            try:
                await bot.edit_message_text(chat_id=uid, message_id=message.message_id, text=text)
                last_text = text
            except Exception as exc:
                module.log.debug("DIGITAL COUNTDOWN edit skipped: %s", exc)
        await asyncio.sleep(min(UPDATE_SECONDS, max(0.5, remaining)))

    try:
        await bot.edit_message_text(
            chat_id=uid,
            message_id=message.message_id,
            text=(
                "🏁 PRIYANITHAN AI SIGNAL — EXPIRY REACHED\n\n"
                f"📈 {pair}\n"
                f"{arrow}\n"
                f"💰 Entry: {entry}\n"
                f"⏱️ Duration: {duration} MIN\n"
                f"🕐 Expiry boundary: {module.fmt_ts(expiry_ts)}\n\n"
                "🔎 VERIFYING EXACT EXPIRY CANDLE…\n"
                "⏳ Please wait for the verified WIN / LOSS result.\n\n"
                "⚠️ RESULT ONLY — AUTO TRADE OFF"
            ),
        )
    except Exception as exc:
        module.log.debug("DIGITAL COUNTDOWN expiry edit skipped: %s", exc)


async def _patched_send_signal(bot, uid, signal):
    module = _app()
    if module is None:
        return
    # IMPORTANT: return immediately after scheduling the UI task. The caller
    # must continue to create the independent exact-expiry result monitor.
    task = asyncio.create_task(
        _countdown_signal(module, bot, uid, signal),
        name=f"digital-countdown-{signal.get('pair', 'asset')}-{uid}",
    )
    module._DIGITAL_COUNTDOWN_TASKS = getattr(module, "_DIGITAL_COUNTDOWN_TASKS", set())
    module._DIGITAL_COUNTDOWN_TASKS.add(task)
    task.add_done_callback(module._DIGITAL_COUNTDOWN_TASKS.discard)


def _patch(module):
    global PATCHED
    if getattr(module, "_DIGITAL_COUNTDOWN_V1", False):
        PATCHED = True
        return True
    original = getattr(module, "send_signal", None)
    if not callable(original):
        return False
    module.send_signal = _patched_send_signal
    module._DIGITAL_COUNTDOWN_V1 = True
    PATCHED = True
    try:
        module.log.warning("DIGITAL COUNTDOWN V1 ACTIVE: live 2s Telegram countdown to exact expiry")
    except Exception:
        pass
    return True


def _boot():
    for _ in range(1800):
        try:
            module = _app()
            if module is not None and _patch(module):
                return
        except Exception:
            pass
        time.sleep(0.1)


threading.Thread(target=_boot, name="digital-countdown-boot", daemon=True).start()

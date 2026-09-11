"""Live monitoring + result/recovery bridge for final signal-engine alerts.

The final signal engine sends approved alerts through send_to_recipients().
This patch keeps that path read-only but connects it to the native result
verification and strict one-shot Candice recovery lifecycle. No broker order
is ever placed, changed, or closed.
"""
import asyncio
import re
import sys
import threading
import time

PATCHED = False
UPDATE_SECONDS = 30


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _parse_signal(text):
    pair_m = re.search(r"📈\s*([^\n]+)", text)
    dir_m = re.search(r"(?:⬆️|⬇️)\s*(UP|DOWN)", text, re.I)
    entry_m = re.search(r"💰\s*Entry:\s*([^\n]+)", text)
    dur_m = re.search(r"⏱️\s*Duration:\s*(\d+)\s*MIN", text, re.I)
    if not (pair_m and dir_m and entry_m and dur_m):
        return None
    try:
        entry = float(entry_m.group(1).strip())
        duration = int(dur_m.group(1))
    except (TypeError, ValueError):
        return None
    return {
        "pair": pair_m.group(1).strip(),
        "direction": dir_m.group(1).upper(),
        "entry": entry,
        "duration": duration,
        "created_at": time.time(),
    }


def _fmt_price(a, value):
    try:
        return a.fmt_price(value)
    except Exception:
        return str(value)


def _clock(seconds):
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


async def _monitor(a, bot, recipients, signal):
    pair = signal["pair"]
    direction = signal["direction"]
    entry = signal["entry"]
    expiry_ts = signal["created_at"] + signal["duration"] * 60
    last_update = None

    while True:
        remaining = expiry_ts - time.time()
        if remaining <= 0:
            return
        live_price = None
        source = "waiting for live candle"
        try:
            client = getattr(a, "ot_client", None)
            if client is not None and client.connection.is_connected:
                raw = await client.market.get_candles(pair, 60, 2)
                df = a.normalize_candles(raw)
                if df is not None and not df.empty:
                    live_price = float(df["close"].iloc[-1])
                    source = "live 1m candle"
            else:
                source = "OlympTrade not connected"
        except Exception as exc:
            source = f"price unavailable: {str(exc)[:70]}"

        if live_price is None:
            state = "⚪ PRICE WAIT"
            detail = source
        else:
            delta = live_price - entry
            if direction == "DOWN":
                delta = -delta
            if delta > 0:
                state = "🟢 DIRECTION ALIGNED"
            elif delta < 0:
                state = "🔴 DIRECTION AGAINST"
            else:
                state = "🟡 AT ENTRY"
            detail = f"📍 Live: {_fmt_price(a, live_price)}\n📐 Move: {'+' if delta >= 0 else ''}{_fmt_price(a, delta)}"

        text = (
            "📡 LIVE SIGNAL UPDATE\n\n"
            f"📈 {pair}\n"
            f"{'⬆️' if direction == 'UP' else '⬇️'} {direction}\n"
            f"💰 Entry: {_fmt_price(a, entry)}\n"
            f"⏳ Remaining: {_clock(remaining)}\n\n"
            f"{state}\n"
            f"{detail}\n\n"
            "🤖 Candice AI: MONITORING\n"
            "🔎 Read-only market check\n"
            "⚠️ MANUAL TRADE — AUTO TRADE OFF"
        )
        if text != last_update:
            try:
                for cid in recipients:
                    await bot.send_message(chat_id=cid, text=text)
                last_update = text
            except Exception as exc:
                a.log.debug("LIVE SIGNAL UPDATE send failed: %s", exc)
        await asyncio.sleep(UPDATE_SECONDS)


async def _patched_send_to_recipients(bot, text):
    a = _app()
    sent = await _ORIGINAL(bot, text)
    if not sent or a is None:
        return sent

    signal = _parse_signal(str(text))
    if signal is None or "🧠 Candice AI: APPROVED" not in str(text):
        return sent

    try:
        recipients = set(a.recipients())
    except Exception:
        recipients = set()
    if not recipients:
        return sent

    # Keep the live 30s stream independent from the expiry/result task.
    key = f"{signal['pair']}:{signal['direction']}"
    active_monitors = getattr(a, "_LIVE_SIGNAL_MONITORS", {})
    old = active_monitors.get(key)
    if old and not old.done():
        old.cancel()
    active_monitors[key] = asyncio.create_task(
        _monitor(a, bot, recipients, signal),
        name=f"live-signal-update-{signal['pair']}-{signal['direction']}",
    )
    a._LIVE_SIGNAL_MONITORS = active_monitors

    # The standalone signal engine does not call app.monitor_result(). Bridge
    # every delivered signal into that lifecycle so exact result verification
    # and the existing strict one-shot recovery AI are always reached.
    monitor_result = getattr(a, "monitor_result", None)
    if callable(monitor_result):
        result_tasks = getattr(a, "_SIGNAL_RESULT_TASKS", {})
        for uid in recipients:
            uid = int(uid)
            state = {
                "pair": signal["pair"],
                "direction": signal["direction"],
                "price": signal["entry"],
                "duration": signal["duration"],
                "created_at": signal["created_at"],
                "ai_confidence": 0,
                "confidence": 0,
                "trend_5m": "UNKNOWN",
                "candle_time": "signal-engine",
                "ai_reason": "FINAL SIGNAL ENGINE APPROVED",
            }
            a.active_signal[uid] = state
            old_result = result_tasks.get(uid)
            if old_result and not old_result.done():
                old_result.cancel()
            result_tasks[uid] = asyncio.create_task(
                monitor_result(bot, uid, state),
                name=f"signal-result-{uid}-{signal['pair']}",
            )
        a._SIGNAL_RESULT_TASKS = result_tasks

    a.log.warning(
        "LIVE SIGNAL UPDATE + RESULT BRIDGE ACTIVE: %s %s %sM; 30s updates, exact expiry result, recovery lifecycle",
        signal["pair"], signal["direction"], signal["duration"],
    )
    return sent


_ORIGINAL = None


def _patch():
    global PATCHED, _ORIGINAL
    a = _app()
    if not a:
        return False
    if getattr(a, "_SIGNAL_LIVE_UPDATE_V2", False):
        PATCHED = True
        return True
    original = getattr(a, "send_to_recipients", None)
    if not callable(original):
        return False
    _ORIGINAL = original
    a.send_to_recipients = _patched_send_to_recipients
    a._SIGNAL_LIVE_UPDATE_V2 = True
    a.log.warning("LIVE SIGNAL UPDATE V2 ACTIVE: final signals bridge to live monitoring + exact result + recovery")
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

threading.Thread(target=_boot, name="signal-live-update", daemon=True).start()

"""Live read-only AI monitoring for an already-approved signal.

Runs alongside the existing expiry monitor. It observes live ticks, sends a
30-second market-status update, and optionally asks the configured AI provider
for a fresh analysis. It never creates, modifies, or closes broker orders.
"""
import asyncio
import re
import sys
import time
from zoneinfo import ZoneInfo

_UAE = ZoneInfo("Asia/Dubai")
_SIGNAL_RE = re.compile(
    r"🔥\s*PRIYANITHAN AI SIGNAL\s*🔥.*?"
    r"📈\s*([A-Z0-9_]+).*?"
    r"(?:⬆️|⬇️)\s*(UP|DOWN).*?"
    r"💰\s*Entry:\s*([0-9.]+).*?"
    r"⏱️\s*(?:Expiry|Duration):\s*(1|2|3|4|5|10|15)\s*MIN", re.S)


def _price(module, pair):
    ticks = getattr(module, "latest_ticks", {})
    item = ticks.get(str(pair).upper()) if isinstance(ticks, dict) else None
    if isinstance(item, dict):
        for key in ("price", "p", "last", "close", "value", "ask", "bid"):
            try:
                v = item.get(key)
                if v is not None and float(v) > 0:
                    return float(v)
            except (TypeError, ValueError):
                pass
    return None


def _install_tick_capture(module):
    if getattr(module, "_LIVE_TICK_CAPTURE_V1", False):
        return
    module.latest_ticks = getattr(module, "latest_ticks", {})
    original = getattr(module, "on_tick", None)
    if not callable(original):
        return

    async def on_tick(message):
        try:
            candidates = []
            if isinstance(message, dict):
                candidates.append(message)
                data = message.get("d")
                if isinstance(data, dict):
                    candidates.append(data)
                if isinstance(data, list):
                    candidates.extend(x for x in data if isinstance(x, dict))
            for item in candidates:
                pair = str(item.get("pair") or item.get("symbol") or item.get("asset") or item.get("name") or "").strip().upper()
                if not pair:
                    continue
                value = None
                for key in ("price", "p", "last", "close", "value", "ask", "bid"):
                    try:
                        if item.get(key) is not None:
                            value = float(item[key])
                            break
                    except (TypeError, ValueError):
                        pass
                if value is not None and value > 0:
                    module.latest_ticks[pair] = {"price": value, "ts": time.time()}
        except Exception:
            module.log.exception("LIVE TICK CAPTURE ERROR")
        return await original(message)

    module.on_tick = on_tick
    module._LIVE_TICK_CAPTURE_V1 = True
    module.log.info("LIVE TICK CAPTURE V1 INSTALLED")


async def _ai_snapshot(module, signal, current_price, elapsed, remaining):
    call_ai = getattr(module, "call_ai", None)
    if not callable(call_ai):
        return None
    entry = float(signal["entry"])
    move = ((current_price - entry) / entry * 100.0) if entry else 0.0
    prompt = (
        "You are a read-only live trade monitor. Do not create a new trade signal. "
        "Analyze the already-approved manual signal using only the supplied live state. "
        "Return JSON only with keys: status, confidence, bias, reason. "
        "status must be MONITORING, STRENGTHENING, WEAKENING, or REVERSAL_RISK. "
        f"Pair={signal['pair']}; original_direction={signal['direction']}; "
        f"entry={entry}; current_price={current_price}; move_pct={move:.5f}; "
        f"elapsed_sec={elapsed:.0f}; remaining_sec={remaining:.0f}."
    )
    try:
        result = await asyncio.to_thread(call_ai, prompt)
        if isinstance(result, tuple):
            result, err = result
            if err or not isinstance(result, dict):
                return None
        return result if isinstance(result, dict) else None
    except Exception as exc:
        module.log.warning("LIVE AI MONITOR CALL FAILED: %s", exc)
        return None


async def _live_monitor(module, signal):
    bot = signal.get("_bot")
    uid = signal.get("uid")
    if bot is None:
        return
    sender = getattr(module, "send_to_recipients", None)
    if not callable(sender):
        # Native app.py uses send_text(bot, text, chat_id), while older signal
        # engines use send_to_recipients(bot, text). Support both paths.
        send_text = getattr(module, "send_text", None)
        if not callable(send_text) or uid is None:
            module.log.warning("LIVE AI MONITOR: no Telegram sender available")
            return
        async def sender(target_bot, text):
            return await send_text(target_bot, text, int(uid))

    start = float(signal.get("signal_time", time.time()))
    expiry = int(signal.get("expiry_min", 5)) * 60
    last_ai = 0.0
    first = True
    while True:
        elapsed = max(0.0, time.time() - start)
        remaining = expiry - elapsed
        if remaining <= 0 or signal.get("status") != "PENDING":
            return
        current = _price(module, signal["pair"])
        if current is None:
            getter = getattr(module, "get_candles", None) or getattr(module, "get_ot_candles", None)
            if callable(getter):
                try:
                    data = await getter(signal["pair"], 60, 80, 90) if getattr(module, "get_candles", None) is getter else await getter(signal["pair"], 60, 120)
                    df = data[0] if isinstance(data, tuple) else data
                    if df is not None and not getattr(df, "empty", True):
                        current = float(df["close"].iloc[-1])
                except Exception:
                    current = None
        if current is not None:
            direction = str(signal["direction"]).upper()
            entry = float(signal["entry"])
            move = ((current - entry) / entry * 100.0) if entry else 0.0
            aligned = (direction == "UP" and current >= entry) or (direction == "DOWN" and current <= entry)
            ai = None
            if first or time.time() - last_ai >= 60:
                ai = await _ai_snapshot(module, signal, current, elapsed, remaining)
                last_ai = time.time()
            if ai:
                status = str(ai.get("status", "MONITORING")).upper()
                conf = ai.get("confidence", "?")
                reason = str(ai.get("reason", ""))[:180]
                text = (
                    "🔴 LIVE AI MONITOR\n\n"
                    f"📈 {signal['pair']} — {direction}\n"
                    f"💰 Entry: {entry}\n"
                    f"📍 Live: {current}\n"
                    f"📐 Move: {move:+.4f}%\n"
                    f"🤖 AI: {status} ({conf}%)\n"
                    f"🧠 {reason}\n"
                    f"⏳ Remaining: {max(0, int(remaining))}s\n"
                    "⚠️ MONITOR ONLY — AUTO TRADE OFF"
                )
            else:
                text = (
                    "🔴 LIVE MARKET MONITOR\n\n"
                    f"📈 {signal['pair']} — {direction}\n"
                    f"💰 Entry: {entry}\n"
                    f"📍 Live: {current}\n"
                    f"📐 Move: {move:+.4f}%\n"
                    f"📊 Direction status: {'ALIGNED' if aligned else 'AGAINST ENTRY'}\n"
                    f"⏳ Remaining: {max(0, int(remaining))}s\n"
                    "⚠️ MONITOR ONLY — AUTO TRADE OFF"
                )
            try:
                await sender(bot, text)
            except Exception:
                module.log.exception("LIVE AI MONITOR SEND FAILED")
        first = False
        await asyncio.sleep(min(30, max(1, remaining)))


def _patch_monitor(module, monitor_mod):
    if getattr(monitor_mod, "_LIVE_AI_MONITOR_V1", False):
        return
    original = getattr(monitor_mod, "monitor_signal", None)
    if not callable(original):
        return

    async def monitored_signal(signal, get_price, send_result):
        live_task = asyncio.create_task(_live_monitor(module, signal), name=f"live-ai-monitor-{signal.get('pair','asset')}")
        try:
            return await original(signal, get_price, send_result)
        finally:
            live_task.cancel()
            try:
                await live_task
            except asyncio.CancelledError:
                pass
            except Exception:
                module.log.exception("LIVE AI MONITOR CLEANUP FAILED")

    monitor_mod.monitor_signal = monitored_signal
    monitor_mod._LIVE_AI_MONITOR_V1 = True
    module.log.info("LIVE AI MONITOR V1 INSTALLED: 30s market updates + 60s AI re-analysis until expiry")


def _boot():
    for _ in range(1800):
        try:
            module = sys.modules.get("__main__")
            if module is None or not getattr(module, "__file__", "").endswith("app.py"):
                module = sys.modules.get("app")
            if module is not None:
                _install_tick_capture(module)
                monitor_mod = sys.modules.get("trade_result_monitor")
                if monitor_mod is None:
                    try:
                        import trade_result_monitor as monitor_mod
                    except Exception:
                        monitor_mod = None
                if monitor_mod is not None:
                    _patch_monitor(module, monitor_mod)
                    if getattr(monitor_mod, "_LIVE_AI_MONITOR_V1", False):
                        return
        except Exception:
            pass
        time.sleep(0.1)

threading.Thread(target=_boot, name="live-ai-monitor-boot", daemon=True).start()

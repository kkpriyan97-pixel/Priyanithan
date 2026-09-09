"""Read-only signal outcome monitor and delivery policy.

Tracks approved alert signals and evaluates the market price at expiry.
Never places, modifies, or closes broker trades.
"""
import asyncio
import re
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

ALLOWED_EXPIRIES = (1, 2, 3, 5, 10, 15)
_UAE = ZoneInfo("Asia/Dubai")
_SIGNAL_RE = re.compile(
    r"🔥\s*PRIYANITHAN AI SIGNAL\s*🔥.*?"
    r"📈\s*([A-Z0-9_]+).*?"
    r"(?:⬆️|⬇️)\s*(UP|DOWN).*?"
    r"💰\s*Entry:\s*([0-9.]+).*?"
    r"⏱️\s*Expiry:\s*(1|2|3|5|10|15)\s*MIN",
    re.S,
)


def register_signal(store, result, ai, signal_time=None):
    duration = int(ai.get("duration_min", 5))
    if duration not in ALLOWED_EXPIRIES:
        duration = 5
    created = float(signal_time if signal_time is not None else time.time())
    signal_id = f"{result['pair']}:{created:.3f}:{result['price']}:{ai.get('direction')}"
    store[signal_id] = {
        "pair": result["pair"],
        "direction": str(ai.get("direction", "")).upper(),
        "entry": float(result["price"]),
        "signal_time": created,
        "expiry_min": duration,
        "status": "PENDING",
    }
    return signal_id


def evaluate_outcome(entry, expiry_price, direction):
    entry = float(entry)
    expiry_price = float(expiry_price)
    direction = str(direction).upper()
    if expiry_price == entry:
        return "DRAW"
    if direction == "UP":
        return "WIN" if expiry_price > entry else "LOSS"
    if direction == "DOWN":
        return "WIN" if expiry_price < entry else "LOSS"
    return "DRAW"


async def monitor_signal(signal, get_price, send_result):
    wait_seconds = max(1, int(signal["expiry_min"] * 60 - (time.time() - signal["signal_time"])))
    await asyncio.sleep(wait_seconds)
    expiry_price = None
    for attempt in range(4):
        try:
            expiry_price = await get_price(signal["pair"])
        except Exception:
            expiry_price = None
        if expiry_price is not None:
            break
        if attempt < 3:
            await asyncio.sleep(5)
    if expiry_price is None:
        signal["status"] = "UNRESOLVED"
        await send_result(signal, "UNRESOLVED")
        return "UNRESOLVED"
    outcome = evaluate_outcome(signal["entry"], expiry_price, signal["direction"])
    signal["expiry_price"] = float(expiry_price)
    signal["status"] = outcome
    await send_result(signal, outcome)
    return outcome


# ============================================================
# SINGLE-CONFIRMED-SIGNAL DELIVERY POLICY
# ============================================================
def _install_single_confirmed_signal_policy():
    for _ in range(1800):
        module = sys.modules.get("__main__")
        if module is None or not getattr(module, "__file__", "").endswith("app.py"):
            module = sys.modules.get("app")
        if module is not None:
            original_scan = getattr(module, "scan_cycle", None)
            original_send = getattr(module, "send_to_recipients", None)
            if original_scan is not None and original_send is not None:
                if getattr(module, "_SINGLE_CONFIRMED_SIGNAL_POLICY", False):
                    return
                async def single_confirmed_scan(application):
                    collected = []
                    async def collect_send(bot, text):
                        collected.append((bot, str(text)))
                        return True
                    module.send_to_recipients = collect_send
                    try:
                        await original_scan(application)
                    finally:
                        module.send_to_recipients = original_send
                    signal_items = []
                    for bot, text in collected:
                        match = _SIGNAL_RE.search(text)
                        if not match:
                            continue
                        pair, direction, entry, expiry = match.groups()
                        ai_match = re.search(r"🤖\s*AI Confidence:\s*(\d+)%", text)
                        tech_match = re.search(r"📊\s*Technical:\s*(\d+)%", text)
                        signal_items.append({
                            "bot": bot, "text": text, "pair": pair,
                            "direction": direction, "entry": float(entry),
                            "expiry": int(expiry),
                            "ai_conf": int(ai_match.group(1)) if ai_match else 0,
                            "tech_conf": int(tech_match.group(1)) if tech_match else 0,
                        })
                    if signal_items:
                        signal_items.sort(key=lambda x: (x["ai_conf"], x["tech_conf"]), reverse=True)
                        chosen = signal_items[0]
                        await original_send(chosen["bot"], chosen["text"])
                        for item in signal_items[1:]:
                            module.log.info(
                                "SIGNAL SUPPRESSED: pair=%s direction=%s AI=%s%% Technical=%s%%; selected=%s AI=%s%% Technical=%s%%",
                                item["pair"], item["direction"], item["ai_conf"], item["tech_conf"],
                                chosen["pair"], chosen["ai_conf"], chosen["tech_conf"],
                            )
                        module.log.info(
                            "SINGLE CONFIRMED SIGNAL SENT: pair=%s direction=%s AI=%s%% Technical=%s%% expiry=%s min; candidates=%s",
                            chosen["pair"], chosen["direction"], chosen["ai_conf"], chosen["tech_conf"], chosen["expiry"], len(signal_items),
                        )
                    else:
                        for bot, text in collected:
                            if "NO QUALIFIED SIGNAL" in text:
                                await original_send(bot, text)
                                break
                module.scan_cycle = single_confirmed_scan
                module._SINGLE_CONFIRMED_SIGNAL_POLICY = True
                module.log.info("SINGLE CONFIRMED SIGNAL POLICY INSTALLED: max 1 Telegram signal per cycle")
                return
        time.sleep(0.1)


# ============================================================
# FAIL-SAFE RESULT MONITOR INSTALLER
# ============================================================
def _install_result_monitor():
    """Install result monitoring directly from this module if sitecustomize has not yet done so."""
    module = sys.modules.get("__main__")
    if module is None or not getattr(module, "__file__", "").endswith("app.py"):
        module = sys.modules.get("app")
    if module is None or getattr(module, "_RESULT_MONITOR_INSTALLED", False):
        return False
    original_format_signal = getattr(module, "format_signal", None)
    original_send = getattr(module, "send_to_recipients", None)
    if original_format_signal is None or original_send is None:
        return False

    # Claim the installation before wrapping so the sitecustomize installer cannot double-wrap it.
    module._RESULT_MONITOR_INSTALLED = True
    module.pending_signals = getattr(module, "pending_signals", {})
    module.pending_signal_tasks = getattr(module, "pending_signal_tasks", set())

    def patched_format_signal(result, ai):
        text = original_format_signal(result, ai)
        now_text = datetime.now(_UAE).strftime("%H:%M:%S UAE")
        return re.sub(r"🕐\s*[^\n]*UAE", "🕐 " + now_text, text, count=1)

    module.format_signal = patched_format_signal

    async def get_expiry_price(pair):
        ticks = getattr(module, "latest_ticks", {})
        item = ticks.get(str(pair).upper()) if isinstance(ticks, dict) else None
        if isinstance(item, dict):
            for key in ("price", "p", "last", "close", "value", "ask", "bid"):
                try:
                    value = item.get(key)
                    if value is not None and float(value) > 0:
                        return float(value)
                except (TypeError, ValueError):
                    pass
        getter = getattr(module, "get_ot_candles", None)
        if getter is None:
            return None
        df, err = await getter(pair, 60, 120)
        if df is None or getattr(df, "empty", True):
            module.log.warning("SIGNAL RESULT PRICE UNAVAILABLE: pair=%s reason=%s", pair, err)
            return None
        try:
            return float(df["close"].iloc[-1])
        except Exception:
            return None

    async def send_result(signal, outcome):
        icon = {"WIN": "✅", "LOSS": "❌", "DRAW": "➖", "UNRESOLVED": "⚠️"}.get(outcome, "⚠️")
        expiry_price = signal.get("expiry_price", "N/A")
        text = (
            f"{icon} PRIYANITHAN SIGNAL RESULT\n\n"
            f"📈 {signal['pair']}\n"
            f"↕️ {signal['direction']}\n"
            f"💰 Entry: {signal['entry']}\n"
            f"🏁 Expiry Price: {expiry_price}\n"
            f"⏱️ Expiry: {signal['expiry_min']} MIN\n"
            f"📊 Market Result: {outcome}\n\n"
            f"⚠️ SIGNAL RESULT ONLY — MANUAL TRADE / AUTO TRADE OFF"
        )
        bot = signal.get("_bot")
        if bot is None:
            app_obj = getattr(module, "telegram_application", None)
            bot = getattr(app_obj, "bot", None) if app_obj is not None else None
        if bot is None:
            module.log.error("SIGNAL RESULT SEND FAILED: Telegram bot object unavailable")
            return False
        sent = await original_send(bot, text)
        module.log.info("SIGNAL RESULT SENT: pair=%s outcome=%s sent=%s entry=%s expiry_price=%s", signal.get("pair"), outcome, sent, signal.get("entry"), expiry_price)
        return sent

    async def monitor_one(signal):
        try:
            return await monitor_signal(signal, get_expiry_price, send_result)
        except Exception as exc:
            module.log.exception("SIGNAL RESULT MONITOR ERROR: pair=%s error=%s", signal.get("pair"), exc)
            signal["status"] = "UNRESOLVED"
            try:
                await send_result(signal, "UNRESOLVED")
            except Exception:
                module.log.exception("SIGNAL RESULT UNRESOLVED SEND FAILED: pair=%s", signal.get("pair"))
            return "UNRESOLVED"

    async def patched_send(bot, text):
        sent = await original_send(bot, text)
        if not sent:
            return sent
        match = _SIGNAL_RE.search(str(text))
        if not match:
            return sent
        try:
            pair, direction, entry_text, expiry_text = match.groups()
            now_ts = time.time()
            result = {"pair": pair, "price": float(entry_text), "candle_time": datetime.now(_UAE).strftime("%H:%M:%S UAE")}
            ai = {"direction": direction, "duration_min": int(expiry_text)}
            signal_id = register_signal(module.pending_signals, result, ai, signal_time=now_ts)
            signal = module.pending_signals[signal_id]
            signal["_bot"] = bot
            task = asyncio.create_task(monitor_one(signal))
            module.pending_signal_tasks.add(task)
            task.add_done_callback(module.pending_signal_tasks.discard)
            module.log.info("SIGNAL RESULT MONITOR STARTED: pair=%s direction=%s expiry=%s min signal_time=%s", pair, direction, expiry_text, datetime.now(_UAE).strftime("%H:%M:%S UAE"))
        except Exception:
            module.log.exception("SIGNAL RESULT MONITOR REGISTER FAILED")
        return sent

    module.send_to_recipients = patched_send
    module.log.info("READ-ONLY SIGNAL RESULT MONITOR INSTALLED (FAIL-SAFE)")
    return True


def _bootstrap():
    for _ in range(1800):
        _install_single_confirmed_signal_policy()
        if _install_result_monitor() or getattr(sys.modules.get("__main__"), "_RESULT_MONITOR_INSTALLED", False):
            return
        time.sleep(0.1)


threading.Thread(target=_bootstrap, name="signal-monitor-bootstrap", daemon=True).start()

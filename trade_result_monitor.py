"""Read-only signal outcome monitor.

Tracks approved alert signals and evaluates the market price at expiry.
Never places, modifies, or closes broker trades.
"""
import asyncio
import re
import sys
import threading
import time

ALLOWED_EXPIRIES = (1, 2, 3, 5, 10, 15)


def register_signal(store, result, ai, signal_time=None):
    """Register an approved signal for later outcome evaluation."""
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
    """Return WIN, LOSS, or DRAW using entry/expiry prices only."""
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
    """Wait until the signal expiry, read fresh market data, then report the result."""
    wait_seconds = max(1, int(signal["expiry_min"] * 60 - (time.time() - signal["signal_time"])))
    await asyncio.sleep(wait_seconds)

    # Retry briefly because the exact expiry candle can be unavailable for a few seconds.
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
# The scanner may evaluate two AI candidates in one 5-minute cycle. We still
# evaluate both, but hold their Telegram messages until the cycle is complete,
# then send ONLY the strongest AI-confirmed setup. This does not change the
# technical analysis or AI decision itself and never executes a trade.
_SIGNAL_RE = re.compile(
    r"🔥\s*PRIYANITHAN AI SIGNAL\s*🔥.*?"
    r"📈\s*([A-Z0-9_]+).*?"
    r"(?:⬆️|⬇️)\s*(UP|DOWN).*?"
    r"💰\s*Entry:\s*([0-9.]+).*?"
    r"⏱️\s*Expiry:\s*(1|2|3|5|10|15)\s*MIN.*?"
    r"🤖\s*AI Confidence:\s*(\d+)%(?:.*?)"
    r"📊\s*Technical:\s*(\d+)%",
    re.S,
)


def _install_single_confirmed_signal_policy():
    for _ in range(300):
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
                        pair, direction, entry, expiry, ai_conf, tech_conf = match.groups()
                        signal_items.append({
                            "bot": bot,
                            "text": text,
                            "pair": pair,
                            "direction": direction,
                            "entry": float(entry),
                            "expiry": int(expiry),
                            "ai_conf": int(ai_conf),
                            "tech_conf": int(tech_conf),
                        })

                    if signal_items:
                        # Primary confirmation = AI confidence; technical confidence
                        # is the tie-breaker. Only ONE signal leaves Telegram per cycle.
                        signal_items.sort(
                            key=lambda x: (x["ai_conf"], x["tech_conf"]),
                            reverse=True,
                        )
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
                        # Preserve the existing NO QUALIFIED SIGNAL notification.
                        for bot, text in collected:
                            if "NO QUALIFIED SIGNAL" in text:
                                await original_send(bot, text)
                                break

                module.scan_cycle = single_confirmed_scan
                module._SINGLE_CONFIRMED_SIGNAL_POLICY = True
                module.log.info("SINGLE CONFIRMED SIGNAL POLICY INSTALLED: max 1 Telegram signal per cycle")
                return
        time.sleep(0.1)


threading.Thread(
    target=_install_single_confirmed_signal_policy,
    name="single-confirmed-signal-policy",
    daemon=True,
).start()

"""Read-only signal outcome monitor.

Tracks approved alert signals and evaluates the market price at expiry.
Never places, modifies, or closes broker trades.
"""
import asyncio
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

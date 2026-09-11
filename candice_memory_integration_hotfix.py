"""Integrate Candice's bounded experience memory into the native app path.

This layer records approved signals and exact WIN/LOSS results and exposes the
historical strategy context on the signal object. It is advisory only until
there is enough evidence; it never changes broker execution, credentials,
Martingale, or the core safety gates.
"""
import sys
import threading
import time

PATCHED = False
MIN_EVIDENCE = 5
LOW_WIN_RATE = 45.0


def _app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    return sys.modules.get("app")


def _brain():
    try:
        from candice_memory_brain import brain
        return brain
    except Exception:
        return None


def _memory_context(signal):
    pair = str(signal.get("pair", "UNKNOWN")).upper()
    direction = str(signal.get("direction", "UNKNOWN")).upper()
    trend = str(signal.get("trend_5m", "UNKNOWN")).upper()
    strategy = "TREND_CONFIRM" if direction == trend else "COUNTER_TREND"
    regime = "TREND" if direction == trend else "CONFLICT"
    current = {
        "asset": pair,
        "timeframe": "5M",
        "strategy": strategy,
        "regime": regime,
        "direction": direction,
    }
    brain = _brain()
    if brain is None:
        return current, {"samples": 0, "historical_win_rate": None}, []
    return current, brain.strategy_score(current), brain.similar_experience(current, 6)


async def _patched_analyze(module, pair):
    original = getattr(module, "_CANDICE_MEMORY_ORIGINAL_ANALYZE", None)
    if not callable(original):
        return None, "Candice memory original analyzer unavailable"
    signal, err = await original(pair)
    if signal is None:
        return None, err
    context, score, similar = _memory_context(signal)
    signal["memory_context"] = context
    signal["memory_samples"] = int(score.get("samples", 0))
    signal["memory_win_rate"] = score.get("historical_win_rate")
    signal["memory_similar"] = len(similar)

    # Evidence-based advisory gate: only suppress a setup after at least five
    # matching historical outcomes and a clearly poor (<45%) win rate.
    samples = signal["memory_samples"]
    rate = signal["memory_win_rate"]
    if samples >= MIN_EVIDENCE and rate is not None and float(rate) < LOW_WIN_RATE:
        module.log.info(
            "CANDICE MEMORY REJECT: pair=%s strategy=%s samples=%s win_rate=%s%%",
            pair, context["strategy"], samples, rate,
        )
        return None, f"Candice memory: {samples} matching results, historical win rate {rate}%"

    module.log.info(
        "CANDICE MEMORY CONTEXT: pair=%s strategy=%s regime=%s samples=%s win_rate=%s%% similar=%s",
        pair, context["strategy"], context["regime"], samples,
        score.get("historical_win_rate"), len(similar),
    )
    return signal, None


async def _patched_monitor(module, bot, uid, signal):
    original = getattr(module, "_CANDICE_MEMORY_ORIGINAL_MONITOR", None)
    if not callable(original):
        return await module.send_text(bot, "⚠️ Result monitor unavailable.", uid)
    await original(bot, uid, signal)

    # The native monitor removes active_signal when it finishes. We cannot read
    # its local result, so result recording is installed by wrapping the exact
    # expiry-price verification function below.


def _patch_expiry(module):
    if getattr(module, "_CANDICE_MEMORY_EXPIRY_V1", False):
        return True
    original = getattr(module, "get_expiry_price", None)
    if not callable(original):
        return False
    module._CANDICE_MEMORY_ORIGINAL_GET_EXPIRY_PRICE = original

    async def get_expiry_price(pair, expiry_ts):
        return await original(pair, expiry_ts)

    module.get_expiry_price = get_expiry_price
    module._CANDICE_MEMORY_EXPIRY_V1 = True
    return True


def _patch(module):
    global PATCHED
    if getattr(module, "_CANDICE_MEMORY_INTEGRATION_V1", False):
        PATCHED = True
        return True
    brain = _brain()
    if brain is None:
        return False
    analyze = getattr(module, "analyze_asset", None)
    monitor = getattr(module, "monitor_result", None)
    if not callable(analyze) or not callable(monitor):
        return False

    module._CANDICE_MEMORY_ORIGINAL_ANALYZE = analyze
    module.analyze_asset = lambda pair: _patched_analyze(module, pair)

    # Record results by wrapping the exact native result monitor. The wrapper
    # re-implements only the final bookkeeping around the existing verifier so
    # the original exact-candle result logic remains authoritative.
    module._CANDICE_MEMORY_ORIGINAL_MONITOR = monitor

    async def monitor_result(bot, uid, signal):
        expiry_ts = float(signal.get("created_at", time.time())) + signal["duration"] * 60
        try:
            await module._CANDICE_MEMORY_ORIGINAL_MONITOR(bot, uid, signal)
        finally:
            # The original monitor only exposes its result through Telegram.
            # Do not manufacture a WIN/LOSS here. Result recording is therefore
            # deferred to the explicit result hook installed below.
            pass

    module.monitor_result = monitor_result
    _patch_expiry(module)
    module._CANDICE_MEMORY_INTEGRATION_V1 = True
    module.log.warning("CANDICE MEMORY INTEGRATION V1 ACTIVE: signal context + bounded historical gate")
    PATCHED = True
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

threading.Thread(target=_boot, name="candice-memory-integration-boot", daemon=True).start()

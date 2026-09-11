"""Integrate Candice's bounded experience memory into the native app path.

Records approved signals and exact WIN/LOSS results, and uses only sufficiently
large historical evidence as a conservative setup filter. It never changes
broker execution, credentials, Martingale, or the exact-candle verifier.
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

    # Do not let a tiny sample influence decisions. Only after five matching
    # outcomes can poor historical performance suppress a new setup.
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


def _record_signal(module, signal):
    brain = _brain()
    if brain is None or signal.get("_memory_recorded"):
        return
    context = signal.get("memory_context") or _memory_context(signal)[0]
    brain.record_signal({
        "asset": context["asset"],
        "timeframe": context["timeframe"],
        "strategy": context["strategy"],
        "regime": context["regime"],
        "direction": str(signal.get("direction", "UNKNOWN")).upper(),
        "duration": int(signal.get("duration", 5)),
        "entry": float(signal.get("price", 0.0)),
        "ai_confidence": signal.get("ai_confidence"),
        "technical_confidence": signal.get("confidence"),
        "created_at": signal.get("created_at", time.time()),
    })
    signal["_memory_recorded"] = True
    module.log.info(
        "CANDICE MEMORY SIGNAL RECORDED: %s %s %sM",
        context["asset"], context["strategy"], signal.get("duration", 5),
    )


async def _patched_monitor(module, bot, uid, signal):
    """Exact native result monitor with memory recording after verification."""
    expiry_ts = float(signal.get("created_at", time.time())) + int(signal["duration"]) * 60
    await module.asyncio.sleep(max(1.0, expiry_ts - time.time()) if hasattr(module, "asyncio") else max(1.0, expiry_ts - time.time()))
    expiry, expiry_price_ts, source = await module.get_expiry_price(signal["pair"], expiry_ts)
    if expiry is None:
        await module.send_text(
            bot,
            f"⚠️ RESULT UNRESOLVED — {signal['pair']}\nExpiry boundary price unavailable: {source}\nNo result was guessed.",
            uid,
        )
        module.active_signal.pop(uid, None)
        return

    result = module.verify_result(float(signal["price"]), expiry, signal["direction"])
    await module.send_text(
        bot,
        "📊 TRADE RESULT\n\n"
        f"📈 {signal['pair']}\n"
        f"{'⬆️' if signal['direction']=='UP' else '⬇️'} {signal['direction']}\n"
        f"💰 Entry: {module.fmt_price(signal['price'])}\n"
        f"🏁 Expiry: {module.fmt_price(expiry)}\n"
        f"⏱️ Duration: {signal['duration']} MIN\n"
        f"🕐 Expiry boundary: {module.fmt_ts(expiry_ts)}\n"
        f"🔎 Verification: {source}\n"
        f"📌 Price candle: {module.fmt_ts(expiry_price_ts)}\n\n"
        f"{'✅' if result=='WIN' else '❌' if result=='LOSS' else '➖'} {result}\n\n"
        "⚠️ RESULT ONLY — AUTO TRADE OFF",
        uid,
    )

    brain = _brain()
    context = signal.get("memory_context") or _memory_context(signal)[0]
    if brain is not None and result in {"WIN", "LOSS"}:
        lesson = (
            "Setup confirmed; preserve the observed trend-confirmation conditions."
            if result == "WIN"
            else "Review entry timing, regime alignment, candle structure, and expiry."
        )
        brain.record_result({
            "asset": context["asset"],
            "timeframe": context["timeframe"],
            "strategy": context["strategy"],
            "regime": context["regime"],
            "direction": str(signal.get("direction", "UNKNOWN")).upper(),
            "outcome": result,
            "entry": float(signal.get("price", 0.0)),
            "expiry": float(expiry),
            "duration": int(signal.get("duration", 5)),
            "lesson": lesson,
        })
        module.log.info(
            "CANDICE MEMORY RESULT RECORDED: %s %s outcome=%s",
            context["asset"], context["strategy"], result,
        )

    module.active_signal.pop(uid, None)
    if result == "LOSS":
        module.selected_asset.pop(uid, None)
        await module.send_asset_menu(
            bot, uid, "🔁 LOSS → AI will re-check live assets. Choose the next asset."
        )


def _patch(module):
    global PATCHED
    if getattr(module, "_CANDICE_MEMORY_INTEGRATION_V2", False):
        PATCHED = True
        return True
    brain = _brain()
    analyze = getattr(module, "analyze_asset", None)
    monitor = getattr(module, "monitor_result", None)
    send_signal = getattr(module, "send_signal", None)
    if brain is None or not callable(analyze) or not callable(monitor) or not callable(send_signal):
        return False

    module._CANDICE_MEMORY_ORIGINAL_ANALYZE = analyze
    module.analyze_asset = lambda pair: _patched_analyze(module, pair)

    module._CANDICE_MEMORY_ORIGINAL_MONITOR = monitor
    module.monitor_result = lambda bot, uid, signal: _patched_monitor(module, bot, uid, signal)

    original_send = send_signal
    module._CANDICE_MEMORY_ORIGINAL_SEND_SIGNAL = original_send

    async def send_signal_with_memory(bot, uid, signal):
        _record_signal(module, signal)
        return await original_send(bot, uid, signal)

    module.send_signal = send_signal_with_memory
    module._CANDICE_MEMORY_INTEGRATION_V2 = True
    module.log.warning(
        "CANDICE MEMORY INTEGRATION V2 ACTIVE: signal + exact WIN/LOSS learning"
    )
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

"""Candice 24/7 observation/research brain.

This is a read-only market-observation layer. It continuously samples fresh
market candles, derives bounded regime/strategy metrics for each timeframe,
and stores those observations in Candice experience memory. It never sends a
signal, places a broker order, changes model weights/code, or weakens risk
filters. Signal generation remains a separate 2-hour-session concern.
"""
from __future__ import annotations

import asyncio
import math
import sys
import threading
import time


PATCHED = False
RESEARCH_INTERVAL_SECONDS = 60
TIMEFRAMES = (1, 3, 5, 10, 15)
MAX_ASSETS_PER_CYCLE = 30


def _app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    return sys.modules.get("app")


def _num(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _frame_metrics(df):
    if df is None or len(df) < 5:
        return None
    tail = df.tail(min(10, len(df))).copy()
    first = _num(tail["open"].iloc[0])
    last = _num(tail["close"].iloc[-1])
    if first <= 0:
        return None
    momentum_pct = ((last - first) / first) * 100.0
    ranges = (tail["high"] - tail["low"]).abs()
    bodies = (tail["close"] - tail["open"]).abs()
    avg_range = _num(ranges.mean())
    avg_body = _num(bodies.mean())
    body_ratio = avg_body / avg_range if avg_range > 0 else 0.0
    if abs(momentum_pct) < 0.03 and body_ratio < 0.35:
        regime = "RANGE"
    elif abs(momentum_pct) >= 0.10 and body_ratio >= 0.45:
        regime = "TREND_UP" if momentum_pct > 0 else "TREND_DOWN"
    else:
        regime = "TRANSITION"
    return {
        "momentum_pct": round(momentum_pct, 5),
        "avg_range": round(avg_range, 8),
        "body_ratio": round(body_ratio, 4),
        "regime": regime,
        "close": round(last, 8),
    }


def _research_snapshot(pair, frames):
    valid = {tf: data for tf, data in frames.items() if data}
    if not valid:
        return None
    m5 = valid.get(5) or {}
    m15 = valid.get(15) or {}
    m1 = valid.get(1) or {}
    macro = "UP" if _num(m15.get("momentum_pct")) > 0.03 else "DOWN" if _num(m15.get("momentum_pct")) < -0.03 else "FLAT"
    micro = "UP" if _num(m1.get("momentum_pct")) > 0.03 else "DOWN" if _num(m1.get("momentum_pct")) < -0.03 else "FLAT"
    alignment = "ALIGNED" if micro == macro and micro != "FLAT" else "MIXED"
    strategy = "TREND_CONFIRM" if alignment == "ALIGNED" else "WAIT_PULLBACK_OR_BREAKOUT"
    return {
        "asset": str(pair).upper(),
        "type": "research",
        "timeframe_profiles": {str(tf): data for tf, data in valid.items()},
        "macro_bias": macro,
        "micro_bias": micro,
        "alignment": alignment,
        "strategy_candidate": strategy,
        "observed_at": time.time(),
        "mode": "OBSERVATION_ONLY",
    }


async def _cycle(module):
    brain_module = sys.modules.get("candice_memory_brain")
    brain = getattr(brain_module, "brain", None) if brain_module else None
    if brain is None:
        try:
            import candice_memory_brain
            brain = candice_memory_brain.brain
        except Exception as exc:
            module.log.warning("CANDICE RESEARCH BRAIN memory unavailable: %s", exc)
            return

    catalog = set(getattr(module, "broker_catalog", set()))
    selected = set(getattr(module, "selected_asset", {}).values())
    pairs = list(dict.fromkeys([str(x).upper() for x in selected] + [str(x).upper() for x in catalog]))
    if not pairs:
        pairs = list(getattr(module, "SEED_PAIRS", []))
    pairs = pairs[:MAX_ASSETS_PER_CYCLE]

    observed = 0
    for pair in pairs:
        frames = {}
        for tf in TIMEFRAMES:
            try:
                df, _ = await module.get_candles(pair, tf, 40, 120 if tf == 1 else 900)
                metrics = _frame_metrics(df)
                if metrics:
                    frames[tf] = metrics
            except Exception:
                continue
        snapshot = _research_snapshot(pair, frames)
        if snapshot is None:
            continue
        try:
            brain.observe(snapshot)
            observed += 1
        except Exception as exc:
            module.log.warning("CANDICE RESEARCH MEMORY WRITE FAILED pair=%s: %s", pair, exc)
    module.log.info("CANDICE 24/7 RESEARCH CYCLE: assets=%s observed=%s interval=%ss", len(pairs), observed, RESEARCH_INTERVAL_SECONDS)


async def _loop(module):
    while True:
        try:
            await _cycle(module)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            module.log.exception("CANDICE RESEARCH CYCLE FAILED: %s", exc)
        await asyncio.sleep(RESEARCH_INTERVAL_SECONDS)


def _start(module):
    global PATCHED
    if getattr(module, "_CANDICE_RESEARCH_BRAIN_V1", False):
        PATCHED = True
        return
    module._CANDICE_RESEARCH_BRAIN_V1 = True
    module.log.warning("CANDICE 24/7 RESEARCH BRAIN V1 ACTIVE: observation-only, no signal/order execution")

    async def runner():
        await _loop(module)

    async def start_when_ready():
        await asyncio.sleep(3)
        asyncio.create_task(runner(), name="candice-24-7-research")

    loop = getattr(module, "runtime_loop", None)
    if loop is not None and loop.is_running():
        asyncio.run_coroutine_threadsafe(start_when_ready(), loop)
    else:
        module.log.warning("CANDICE RESEARCH BRAIN waiting for runtime_loop")
    PATCHED = True


def _boot():
    for _ in range(1800):
        try:
            module = _app()
            if module is not None and callable(getattr(module, "get_candles", None)):
                _start(module)
                return
        except Exception:
            pass
        time.sleep(0.1)


threading.Thread(target=_boot, name="candice-research-brain-boot", daemon=True).start()

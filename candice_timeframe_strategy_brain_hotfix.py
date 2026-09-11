"""Candice multi-timeframe strategy scoring brain.

Builds bounded 1M/3M/5M/10M/15M profiles from the 24/7 observation memory and
completed WIN/LOSS evidence. This layer is advisory/conservative only: it can
suppress a setup when mature matching evidence is poor, but it never creates
or approves a signal, lowers confidence gates, modifies code/model weights,
or places broker orders.
"""
from __future__ import annotations

import sys
import threading
import time

PATCHED = False
TIMEFRAMES = (1, 3, 5, 10, 15)
MIN_EVIDENCE = 5
LOW_WIN_RATE = 45.0

STRATEGIES = {
    1: "MICRO_MOMENTUM",
    3: "PULLBACK_MOMENTUM",
    5: "TREND_CONFIRM",
    10: "INTRADAY_STRUCTURE",
    15: "MACRO_CONTEXT",
}


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


def _latest_research(pair):
    brain = _brain()
    if brain is None:
        return None
    wanted = str(pair).upper()
    with getattr(sys.modules.get("candice_memory_brain"), "LOCK"):
        events = list(brain.events)
    for event in reversed(events):
        if event.get("type") != "observation":
            continue
        data = event.get("data", {})
        if str(data.get("asset", "")).upper() == wanted and data.get("type") == "research":
            return data
    return None


def _direction_from_profile(profile):
    regime = str((profile or {}).get("regime", "")).upper()
    momentum = float((profile or {}).get("momentum_pct", 0.0) or 0.0)
    if "UP" in regime or momentum > 0.03:
        return "UP"
    if "DOWN" in regime or momentum < -0.03:
        return "DOWN"
    return "FLAT"


def build_strategy_context(pair, candidate_direction):
    brain = _brain()
    research = _latest_research(pair)
    candidate = str(candidate_direction).upper()
    profiles = (research or {}).get("timeframe_profiles", {})
    rows = []
    mature_bad = []
    aligned = 0
    opposed = 0

    for tf in TIMEFRAMES:
        profile = profiles.get(str(tf), {}) if isinstance(profiles, dict) else {}
        observed_direction = _direction_from_profile(profile)
        regime = str(profile.get("regime", "UNKNOWN")).upper()
        current = {
            "asset": str(pair).upper(),
            "timeframe": f"{tf}M",
            "strategy": STRATEGIES[tf],
            "regime": regime,
            "direction": candidate,
        }
        score = brain.strategy_score(current) if brain is not None else {"samples": 0, "historical_win_rate": None}
        samples = int(score.get("samples", 0))
        rate = score.get("historical_win_rate")
        if observed_direction == candidate:
            aligned += 1
        elif observed_direction not in {"FLAT", "UNKNOWN"}:
            opposed += 1
        if samples >= MIN_EVIDENCE and rate is not None and float(rate) < LOW_WIN_RATE:
            mature_bad.append(tf)
        rows.append({
            "timeframe": f"{tf}M",
            "strategy": STRATEGIES[tf],
            "observed_direction": observed_direction,
            "regime": regime,
            "samples": samples,
            "historical_win_rate": rate,
        })

    return {
        "asset": str(pair).upper(),
        "candidate_direction": candidate,
        "profiles": rows,
        "aligned_timeframes": aligned,
        "opposed_timeframes": opposed,
        "mature_low_score_timeframes": mature_bad,
        "research_available": research is not None,
    }


async def _patched_analyze(module, pair):
    original = getattr(module, "_CANDICE_TF_ORIGINAL_ANALYZE", None)
    if not callable(original):
        return None, "Candice timeframe original analyzer unavailable"
    signal, err = await original(pair)
    if signal is None:
        return None, err

    context = build_strategy_context(pair, signal.get("direction"))
    signal["timeframe_strategy_context"] = context

    # Conservative veto only. A single mature, historically weak matching
    # timeframe is enough to reject; absence of evidence never approves.
    bad = context["mature_low_score_timeframes"]
    if bad:
        module.log.info(
            "CANDICE TIMEFRAME STRATEGY REJECT: pair=%s direction=%s weak=%s",
            pair, signal.get("direction"), bad,
        )
        return None, f"Candice timeframe memory rejected mature weak profile(s): {bad}"

    module.log.info(
        "CANDICE TIMEFRAME STRATEGY: pair=%s direction=%s aligned=%s opposed=%s research=%s",
        pair, signal.get("direction"), context["aligned_timeframes"],
        context["opposed_timeframes"], context["research_available"],
    )
    return signal, None


def _patch(module):
    global PATCHED
    if getattr(module, "_CANDICE_TIMEFRAME_STRATEGY_V1", False):
        PATCHED = True
        return True
    analyze = getattr(module, "analyze_asset", None)
    if not callable(analyze):
        return False
    module._CANDICE_TF_ORIGINAL_ANALYZE = analyze
    module.analyze_asset = lambda pair: _patched_analyze(module, pair)
    module.build_candice_strategy_context = build_strategy_context
    module._CANDICE_TIMEFRAME_STRATEGY_V1 = True
    module.log.warning(
        "CANDICE TIMEFRAME STRATEGY BRAIN V1 ACTIVE: 1M/3M/5M/10M/15M evidence scoring"
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

threading.Thread(target=_boot, name="candice-timeframe-strategy-boot", daemon=True).start()

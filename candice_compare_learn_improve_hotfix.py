"""Candice bounded Compare -> Learn -> Improve feedback layer.

Compares completed WIN/LOSS evidence against the current setup, extracts
repeatable lessons, and injects a compact advisory note into the existing AI
analysis request. It never changes code/model weights, lowers gates, forces a
signal, or places broker orders.
"""
from __future__ import annotations

import math
import re
import sys
import threading
import time

PATCHED = False
MAX_RESULTS = 12
MIN_EVIDENCE = 5


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


def _fields(payload):
    try:
        messages = payload.get("messages", [])
        text = "\n".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))
    except Exception:
        return None, None, None
    def get(label):
        match = re.search(rf"{re.escape(label)}\s*:\s*([A-Za-z0-9_.-]+)", text, re.I)
        return match.group(1).upper() if match else None
    return get("Asset"), get("Direction candidate"), get("5m trend")


def compare_learn(pair, direction, trend):
    brain = _brain()
    current = {
        "asset": str(pair or "UNKNOWN").upper(),
        "timeframe": "5M",
        "strategy": "TREND_CONFIRM" if str(direction).upper() == str(trend).upper() else "COUNTER_TREND",
        "regime": "TREND" if str(direction).upper() == str(trend).upper() else "CONFLICT",
        "direction": str(direction or "UNKNOWN").upper(),
    }
    if brain is None:
        return {"samples": 0, "wins": 0, "losses": 0, "win_rate": None, "lesson": "No experience memory available."}
    ctx = brain.context_for_ai(current)
    strategy = ctx.get("strategy", {})
    results = ctx.get("similar_results", [])[:MAX_RESULTS]
    wins = sum(1 for e in results if str(e.get("data", {}).get("outcome", "")).upper() == "WIN")
    losses = sum(1 for e in results if str(e.get("data", {}).get("outcome", "")).upper() == "LOSS")
    rate = strategy.get("historical_win_rate")
    lesson = "Insufficient evidence: treat history as context only."
    if int(strategy.get("samples", 0)) >= MIN_EVIDENCE and rate is not None:
        if float(rate) >= 60.0:
            lesson = "Historical evidence favors this setup profile; still require current structure confirmation."
        elif float(rate) < 45.0:
            lesson = "Historical evidence is weak; reject if current structure also conflicts. Never force approval."
        else:
            lesson = "Historical evidence is mixed; use conservative confirmation and avoid marginal entries."
    return {
        "strategy_key": strategy.get("key"),
        "samples": int(strategy.get("samples", 0)),
        "wins": int(strategy.get("wins", 0)),
        "losses": int(strategy.get("losses", 0)),
        "win_rate": rate,
        "recent_similar": len(results),
        "recent_wins": wins,
        "recent_losses": losses,
        "lesson": lesson,
    }


def _inject(payload):
    pair, direction, trend = _fields(payload)
    if not pair or not direction:
        return payload
    learning = compare_learn(pair, direction, trend or "UNKNOWN")
    note = (
        "\n\nCANDICE COMPARE -> LEARN -> IMPROVE (ADVISORY EVIDENCE ONLY):\n"
        f"Strategy={learning.get('strategy_key')} samples={learning.get('samples')} "
        f"wins={learning.get('wins')} losses={learning.get('losses')} "
        f"historical_win_rate={learning.get('win_rate')}%\n"
        f"Recent similar={learning.get('recent_similar')} wins={learning.get('recent_wins')} losses={learning.get('recent_losses')}\n"
        f"Lesson={learning.get('lesson')}\n"
        "Use evidence to compare the current setup with completed outcomes. "
        "Never force approval, never lower confidence gates, never change code/model weights, and never place broker orders."
    )
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        for msg in reversed(messages):
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                msg["content"] += note
                break
    return payload


def _patch(module):
    global PATCHED
    if getattr(module, "_CANDICE_COMPARE_LEARN_IMPROVE_V1", False):
        PATCHED = True
        return True
    requests = getattr(module, "requests", None)
    if requests is None or not callable(getattr(requests, "post", None)):
        return False
    original = requests.post
    if getattr(original, "_CANDICE_CLI_WRAPPED", False):
        return False
    def post(url, *args, **kwargs):
        if "openrouter.ai/api/v1/chat/completions" in str(url):
            payload = kwargs.get("json")
            if isinstance(payload, dict):
                kwargs["json"] = _inject(payload)
        return original(url, *args, **kwargs)
    post._CANDICE_CLI_WRAPPED = True
    requests.post = post
    module._CANDICE_COMPARE_LEARN_IMPROVE_V1 = True
    module.log.warning("CANDICE COMPARE LEARN IMPROVE V1 ACTIVE: bounded experience feedback")
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

threading.Thread(target=_boot, name="candice-compare-learn-boot", daemon=True).start()

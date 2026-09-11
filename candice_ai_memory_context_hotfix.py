"""Inject bounded Candice experience context into the existing AI request.

The native analyzer remains the owner of technical gates and AI approval. This
hook only adds historical evidence to the OpenRouter prompt. It never approves
a setup, lowers confidence requirements, changes risk rules, or places orders.
"""
from __future__ import annotations

import copy
import sys
import threading
import time

PATCHED = False


def _app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    return sys.modules.get("app")


def _memory_context(pair: str, direction: str, trend: str):
    try:
        from candice_memory_brain import brain
    except Exception:
        return None
    direction = str(direction).upper()
    trend = str(trend).upper()
    current = {
        "asset": str(pair).upper(),
        "timeframe": "5M",
        "strategy": "TREND_CONFIRM" if direction == trend else "COUNTER_TREND",
        "regime": "TREND" if direction == trend else "CONFLICT",
        "direction": direction,
    }
    context = brain.context_for_ai(current)
    score = context.get("strategy", {})
    similar = context.get("similar_results", [])
    compact = []
    for event in similar[:6]:
        data = event.get("data", {}) if isinstance(event, dict) else {}
        compact.append({
            "outcome": str(data.get("outcome", "UNKNOWN")).upper(),
            "direction": str(data.get("direction", "UNKNOWN")).upper(),
            "duration": data.get("duration"),
            "lesson": str(data.get("lesson", ""))[:160],
        })
    return {
        "strategy_key": score.get("key"),
        "samples": int(score.get("samples", 0)),
        "wins": int(score.get("wins", 0)),
        "losses": int(score.get("losses", 0)),
        "historical_win_rate": score.get("historical_win_rate"),
        "similar_results": compact,
    }


def _extract_fields(payload):
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(messages, list):
        return None
    text = "\n".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))
    if "Asset:" not in text or "Direction candidate:" not in text:
        return None
    def field(name):
        marker = name + ":"
        for line in text.splitlines():
            if line.strip().startswith(marker):
                return line.split(":", 1)[1].strip()
        return ""
    return field("Asset"), field("Direction candidate"), field("5m trend")


def _patch(module):
    global PATCHED
    if getattr(module, "_CANDICE_AI_MEMORY_CONTEXT_V1", False):
        PATCHED = True
        return True
    requests = getattr(module, "requests", None)
    if requests is None or not callable(getattr(requests, "post", None)):
        return False
    original = requests.post

    def post_with_memory(url, *args, **kwargs):
        if "openrouter.ai/api/v1/chat/completions" not in str(url):
            return original(url, *args, **kwargs)
        payload = kwargs.get("json")
        fields = _extract_fields(payload) if isinstance(payload, dict) else None
        if fields:
            pair, direction, trend = fields
            context = _memory_context(pair, direction, trend)
            if context is not None:
                body = copy.deepcopy(payload)
                messages = body.get("messages", [])
                memory_note = (
                    "\\n\\nCANDICE EXPERIENCE MEMORY (evidence only; do not override current market data):\\n"
                    f"Samples: {context['samples']} | Wins: {context['wins']} | Losses: {context['losses']} | "
                    f"Historical win rate: {context['historical_win_rate']}\\n"
                    f"Strategy key: {context['strategy_key']}\\n"
                    f"Similar completed outcomes: {context['similar_results']}\\n"
                    "Use this only as supporting evidence. If current technical structure conflicts, REJECT. "
                    "Never force approval and never lower the minimum confidence requirement."
                )
                if messages and isinstance(messages[-1], dict):
                    messages[-1] = dict(messages[-1])
                    messages[-1]["content"] = str(messages[-1].get("content", "")) + memory_note
                body["messages"] = messages
                kwargs = dict(kwargs)
                kwargs["json"] = body
                module.log.info(
                    "CANDICE AI MEMORY CONTEXT INJECTED: pair=%s samples=%s win_rate=%s",
                    pair, context["samples"], context["historical_win_rate"],
                )
        return original(url, *args, **kwargs)

    requests.post = post_with_memory
    module._CANDICE_AI_MEMORY_CONTEXT_V1 = True
    module._CANDICE_AI_MEMORY_ORIGINAL_REQUESTS_POST = original
    module.log.warning(
        "CANDICE AI MEMORY CONTEXT V1 ACTIVE: experience evidence added to AI prompt"
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

threading.Thread(target=_boot, name="candice-ai-memory-context-boot", daemon=True).start()

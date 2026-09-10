"""Fresh-engine AI runtime patch.

The main engine remains the source of truth. This small module only replaces
its AI request adapter after app.py has finished defining it. It fixes the
observed failure where Groq returned non-JSON text and json.loads() rejected it.
No signal is fabricated when all providers fail.
"""

import json
import re
import sys
import threading
import time

import requests


ALLOWED_DURATIONS = (2, 3, 5, 10, 15)


def _extract_json(text):
    text = str(text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except Exception:
        pass

    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[i:])
            if isinstance(value, dict):
                return value
        except Exception:
            continue
    return None


def _normalize(value):
    if not isinstance(value, dict):
        return None
    decision = str(value.get("decision", "")).upper().strip()
    direction = str(value.get("direction", "")).upper().strip()
    try:
        confidence = int(float(value.get("confidence", 0)))
    except Exception:
        confidence = 0
    try:
        duration = int(float(value.get("duration_min", value.get("duration", 0))))
    except Exception:
        duration = 0
    if decision not in {"APPROVE", "REJECT"}:
        return None
    if direction not in {"UP", "DOWN"}:
        return None
    if not 0 <= confidence <= 100:
        return None
    if duration not in ALLOWED_DURATIONS:
        return None
    return {
        "decision": decision,
        "direction": direction,
        "confidence": confidence,
        "duration_min": duration,
        "reason": str(value.get("reason", "Candice AI decision"))[:220],
    }


def _prose_fallback(text):
    """Parse common model prose only when it contains all required fields."""
    raw = str(text or "")
    upper = raw.upper()
    m_dec = re.search(r"\b(APPROVE|REJECT)\b", upper)
    m_dir = re.search(r"\b(UP|DOWN)\b", upper)
    m_conf = re.search(r"(?:CONFIDENCE|SCORE)\s*[:=\-]?\s*(\d{1,3})\s*%?", upper)
    m_dur = re.search(r"(?:DURATION|EXPIRY)\s*[:=\-]?\s*(2|3|5|10|15)\s*(?:MIN|MINUTE|MINUTES)?", upper)
    if not (m_dec and m_dir and m_conf and m_dur):
        return None
    return _normalize({
        "decision": m_dec.group(1),
        "direction": m_dir.group(1),
        "confidence": m_conf.group(1),
        "duration_min": m_dur.group(1),
        "reason": raw[:220],
    })


def _patched_ai_request(prompt):
    mod = sys.modules.get("__main__") or sys.modules.get("app")
    if mod is None:
        return None, "app module unavailable"

    providers = []
    if getattr(mod, "OPENROUTER_API_KEY", ""):
        providers.append(("OpenRouter", "https://openrouter.ai/api/v1/chat/completions", mod.OPENROUTER_API_KEY, getattr(mod, "OPENROUTER_MODEL", "openrouter/free")))
    if getattr(mod, "CEREBRAS_API_KEY", ""):
        providers.append(("Cerebras", "https://api.cerebras.ai/v1/chat/completions", mod.CEREBRAS_API_KEY, getattr(mod, "CEREBRAS_MODEL", "gpt-oss-120b")))
    if getattr(mod, "GROQ_API_KEY", ""):
        providers.append(("Groq", "https://api.groq.com/openai/v1/chat/completions", mod.GROQ_API_KEY, getattr(mod, "GROQ_MODEL", "openai/gpt-oss-120b")))

    if not providers:
        return None, "No AI provider configured"

    system = (
        "You are Candice, a disciplined professional market analyst. "
        "Use ONLY the supplied live OlympTrade candle/indicator snapshot. "
        "Do not invent market data. Approve only when direction is coherent with "
        "the supplied 1m structure and 5m trend. Return exactly one JSON object "
        "with keys decision, direction, confidence, duration_min, reason. "
        "decision must be APPROVE or REJECT; direction UP or DOWN; confidence 0-100; "
        "duration_min MUST be one of 2,3,5,10,15. Never use 1 minute."
    )
    body = {
        "model": None,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
    }

    last_error = "All AI providers failed"
    for name, url, key, model in providers:
        body["model"] = model
        for attempt in range(2):
            try:
                r = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=body,
                    timeout=30,
                )
                if r.status_code >= 400:
                    # Some OpenAI-compatible endpoints reject response_format.
                    if attempt == 0 and r.status_code in (400, 404, 422):
                        body.pop("response_format", None)
                        continue
                    last_error = f"{name} HTTP {r.status_code}"
                    if mod is not None and hasattr(mod, "log"):
                        mod.log.warning("AI %s failed: HTTP %s", name, r.status_code)
                    break
                data = r.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if isinstance(content, list):
                    content = "".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
                parsed = _normalize(_extract_json(content)) or _prose_fallback(content)
                if parsed is not None:
                    if mod is not None and hasattr(mod, "log"):
                        mod.log.info(
                            "AI %s APPROVED/DECIDED: direction=%s confidence=%s duration=%s decision=%s",
                            name, parsed["direction"], parsed["confidence"], parsed["duration_min"], parsed["decision"],
                        )
                    return parsed, name
                last_error = f"{name} returned unusable AI output"
                if mod is not None and hasattr(mod, "log"):
                    mod.log.warning("AI %s returned non-parseable decision; trying next provider", name)
                break
            except Exception as exc:
                last_error = f"{name}: {exc}"
                if mod is not None and hasattr(mod, "log"):
                    mod.log.warning("AI %s failed: %s", name, exc)
                break
            finally:
                # Small pacing gap avoids burst/rate-limit behavior.
                if attempt == 0:
                    time.sleep(0.4)

    return None, last_error


def _boot():
    for _ in range(1200):
        try:
            mod = sys.modules.get("__main__") or sys.modules.get("app")
            if mod is not None and hasattr(mod, "ai_request"):
                mod.ai_request = _patched_ai_request
                if hasattr(mod, "log"):
                    mod.log.warning("FRESH AI ADAPTER ACTIVE: JSON mode + robust parser + provider fallback")
                return
        except Exception:
            pass
        time.sleep(0.25)


threading.Thread(target=_boot, name="fresh-ai-adapter", daemon=True).start()

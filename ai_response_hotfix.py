"""Runtime hotfix for OpenAI-compatible AI responses.

Only normalizes an otherwise-successful Groq response when ALL required
trading-decision fields are explicitly present. It never invents or approves
missing data. AUTO TRADE remains OFF.
"""
import json
import re
import threading
import requests

_REQUIRED = ("decision", "direction", "confidence", "duration_min", "reason")
_orig_post = requests.post
_installed = False


def _extract(text):
    if not text:
        return None
    text = str(text).strip()
    # First, recover a JSON object from prose/markdown.
    for start in [m.start() for m in re.finditer(r"\\{", text)]:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict) and all(k in obj for k in _REQUIRED):
                return obj
        except Exception:
            pass
    # Some OpenAI-compatible models return labelled lines instead of JSON.
    patterns = {
        "decision": r"(?:decision)\\s*[:=]\\s*([A-Za-z _-]+)",
        "direction": r"(?:direction)\\s*[:=]\\s*([A-Za-z _-]+)",
        "confidence": r"(?:confidence)\\s*[:=]\\s*(\\d{1,3})%?",
        "duration_min": r"(?:duration(?:_min)?|expiry|expiration)\\s*[:=]\\s*(\\d{1,2})\\s*(?:min|minutes?)?",
        "reason": r"(?:reason)\\s*[:=]\\s*(.+?)(?=\\n(?:decision|direction|confidence|duration(?:_min)?|expiry|expiration|reason)\\s*[:=]|$)",
    }
    out = {}
    for key, pat in patterns.items():
        m = re.search(pat, text, re.I | re.S)
        if not m:
            return None
        value = m.group(1).strip().strip("`* ")
        if key == "confidence":
            value = int(value)
        elif key == "duration_min":
            value = int(value)
        else:
            value = value.strip()
        out[key] = value
    return out


def _post(*args, **kwargs):
    response = _orig_post(*args, **kwargs)
    try:
        url = str(args[0] if args else kwargs.get("url", ""))
        if "api.groq.com" not in url or not response.ok:
            return response
        body = response.json()
        choices = body.get("choices") if isinstance(body, dict) else None
        if not choices or not isinstance(choices[0], dict):
            return response
        msg = choices[0].get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, (dict, list)):
            return response
        parsed = _extract(content or choices[0].get("text") or body.get("output_text"))
        if not parsed:
            return response
        decision = str(parsed.get("decision", "")).upper().replace(" ", "_")
        direction = str(parsed.get("direction", "")).upper()
        try:
            confidence = int(parsed.get("confidence"))
            duration = int(parsed.get("duration_min"))
        except Exception:
            return response
        if decision not in {"APPROVE", "REJECT"} or direction not in {"UP", "DOWN", "NO SIGNAL", "NO_SIGNAL"}:
            return response
        if not 0 <= confidence <= 100 or duration not in {1, 2, 3, 5, 10, 15}:
            return response
        parsed["decision"] = decision
        parsed["direction"] = "NO SIGNAL" if direction == "NO_SIGNAL" else direction
        parsed["confidence"] = confidence
        parsed["duration_min"] = duration
        normalized = dict(body)
        normalized["choices"] = [dict(choices[0], message=dict(msg, content=json.dumps(parsed, separators=(",", ":"))))]
        response._content = json.dumps(normalized).encode("utf-8")
        response.headers["Content-Type"] = "application/json"
    except Exception:
        pass
    return response


def install():
    global _installed
    if _installed or requests.post is not _orig_post:
        return
    requests.post = _post
    _installed = True


# Install immediately; the patch is provider-specific and inert for all other APIs.
install()

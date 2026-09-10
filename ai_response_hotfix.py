"""Runtime hotfix for OpenAI-compatible AI responses.

Normalizes successful Groq responses when the model returns an explicit
trading decision but wraps it in prose/markdown or labelled fields instead
of clean JSON. It also requests Groq JSON-object mode so the normal parser
receives machine-readable output. It never invents a decision.
AUTO TRADE remains OFF.
"""
import copy
import json
import re
import requests

_REQUIRED = ("decision", "direction", "confidence", "duration_min", "reason")
_ALLOWED_DURATIONS = {1, 2, 3, 5, 15}
_orig_post = requests.post
_installed = False


def _extract(text):
    if not text:
        return None

    # Structured response content can arrive as a list of content blocks.
    if isinstance(text, list):
        parts = []
        for item in text:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        text = "".join(parts)
    if isinstance(text, dict):
        if all(k in text for k in _REQUIRED):
            return dict(text)
        return None

    text = str(text).strip()

    # JSON object embedded in prose or a markdown code fence.
    for match in re.finditer(r"\{", text):
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[match.start():])
            if isinstance(obj, dict) and all(k in obj for k in _REQUIRED):
                return obj
        except Exception:
            continue

    # Some OpenAI-compatible responses use labelled fields instead of JSON.
    patterns = {
        "decision": r"(?:decision)\s*[:=]\s*(APPROVE|REJECT)\b",
        "direction": r"(?:direction)\s*[:=]\s*(UP|DOWN|NO\s*SIGNAL)\b",
        "confidence": r"(?:confidence)\s*[:=]\s*(\d{1,3})\s*%?",
        "duration_min": r"(?:duration_min|duration|expiry|expiration)\s*[:=]\s*(\d{1,2})\s*(?:min(?:ute)?s?)?\b",
        "reason": r"(?:reason)\s*[:=]\s*(.+?)(?=\n\s*(?:decision|direction|confidence|duration_min|duration|expiry|expiration|reason)\s*[:=]|$)",
    }
    out = {}
    for key, pattern in patterns.items():
        m = re.search(pattern, text, re.I | re.S)
        if not m:
            return None
        value = m.group(1).strip().strip("`* ")
        if key in {"confidence", "duration_min"}:
            try:
                value = int(value)
            except Exception:
                return None
        out[key] = value
    return out


def _post(*args, **kwargs):
    # Groq supports OpenAI-compatible JSON Object Mode. Add it only for Groq,
    # leaving other providers untouched. Never alter the user's decision data.
    try:
        url = str(args[0] if args else kwargs.get("url", ""))
        if "api.groq.com" in url:
            payload = kwargs.get("json")
            if isinstance(payload, dict):
                payload = copy.deepcopy(payload)
                payload["response_format"] = {"type": "json_object"}
                kwargs["json"] = payload
    except Exception:
        pass

    response = _orig_post(*args, **kwargs)
    try:
        url = str(args[0] if args else kwargs.get("url", ""))
        if "api.groq.com" not in url or not response.ok:
            return response

        body = response.json()
        choices = body.get("choices") if isinstance(body, dict) else None
        if not choices or not isinstance(choices[0], dict):
            return response

        first = choices[0]
        msg = first.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None

        # Also inspect common structured-output fields before falling back to text.
        structured = None
        if isinstance(msg, dict):
            structured = msg.get("parsed") or msg.get("json")
        if structured is None and isinstance(first, dict):
            structured = first.get("parsed") or first.get("json")
        parsed = _extract(structured) if structured is not None else None

        if parsed is None:
            text = content or first.get("text") or body.get("output_text")
            parsed = _extract(text)
        if not parsed:
            return response

        decision = str(parsed.get("decision", "")).upper().replace(" ", "_")
        direction = str(parsed.get("direction", "")).upper().replace("  ", " ").strip()
        try:
            confidence = int(parsed.get("confidence"))
            duration = int(parsed.get("duration_min"))
        except Exception:
            return response

        if decision not in {"APPROVE", "REJECT"}:
            return response
        if direction not in {"UP", "DOWN", "NO SIGNAL", "NO_SIGNAL"}:
            return response
        if not 0 <= confidence <= 100 or duration not in _ALLOWED_DURATIONS:
            return response
        if not str(parsed.get("reason", "")).strip():
            return response

        parsed["decision"] = decision
        parsed["direction"] = "NO SIGNAL" if direction == "NO_SIGNAL" else direction
        parsed["confidence"] = confidence
        parsed["duration_min"] = duration
        parsed["reason"] = str(parsed.get("reason", "")).strip()

        normalized = dict(body)
        normalized["choices"] = [
            dict(first, message=dict(msg, content=json.dumps(parsed, separators=(",", ":"))))
        ]
        response._content = json.dumps(normalized).encode("utf-8")
        response.headers["Content-Type"] = "application/json"
    except Exception:
        # Never turn a provider response into a forced trading decision.
        pass
    return response


def install():
    global _installed
    if _installed or requests.post is not _orig_post:
        return
    requests.post = _post
    _installed = True


install()

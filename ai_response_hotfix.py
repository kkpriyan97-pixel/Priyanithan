"""Runtime hotfix for OpenAI-compatible AI responses.

Groq GPT-OSS supports strict Structured Outputs. Request the exact trading
schema so successful Groq calls return machine-readable JSON. Normalize a
few compatible response shapes as a fallback. Never invent a decision.
AUTO TRADE remains OFF.
"""
import copy
import json
import re
import requests

_REQUIRED = ("decision", "direction", "confidence", "duration_min", "reason")
_ALLOWED_DURATIONS = {1, 2, 3, 5, 15}
_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["APPROVE", "REJECT"]},
        "direction": {"type": "string", "enum": ["UP", "DOWN", "NO SIGNAL"]},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "duration_min": {"type": "integer", "enum": sorted(_ALLOWED_DURATIONS)},
        "reason": {"type": "string"},
    },
    "required": list(_REQUIRED),
    "additionalProperties": False,
}
_orig_post = requests.post
_installed = False


def _extract(value):
    if not value:
        return None
    if isinstance(value, dict):
        return dict(value) if all(k in value for k in _REQUIRED) else None
    if isinstance(value, list):
        value = "".join(
            str(item.get("text") or item.get("content") or "") if isinstance(item, dict) else str(item)
            for item in value
        )
    text = str(value).strip()
    for match in re.finditer(r"\{", text):
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[match.start():])
            if isinstance(obj, dict) and all(k in obj for k in _REQUIRED):
                return obj
        except Exception:
            continue
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
        v = m.group(1).strip().strip("`* ")
        if key in {"confidence", "duration_min"}:
            try:
                v = int(v)
            except Exception:
                return None
        out[key] = v
    return out


def _normalize_request(kwargs):
    payload = kwargs.get("json")
    if not isinstance(payload, dict):
        return kwargs
    out = dict(kwargs)
    payload = copy.deepcopy(payload)
    model = str(payload.get("model", "")).lower()
    if model in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "trading_signal_decision",
                "strict": True,
                "schema": _SCHEMA,
            },
        }
        payload["include_reasoning"] = False
        payload.setdefault("reasoning_effort", "low")
        out["json"] = payload
    return out


def _post(*args, **kwargs):
    url = str(args[0] if args else kwargs.get("url", ""))
    if "api.groq.com" in url:
        kwargs = _normalize_request(kwargs)
    response = _orig_post(*args, **kwargs)
    try:
        if "api.groq.com" not in url or not response.ok:
            return response
        body = response.json()
        choices = body.get("choices") if isinstance(body, dict) else None
        if not choices or not isinstance(choices[0], dict):
            return response
        first = choices[0]
        msg = first.get("message") or {}
        structured = None
        if isinstance(msg, dict):
            structured = msg.get("parsed") or msg.get("json")
        if structured is None:
            structured = first.get("parsed") or first.get("json")
        parsed = _extract(structured)
        if parsed is None:
            content = msg.get("content") if isinstance(msg, dict) else None
            parsed = _extract(content or first.get("text") or body.get("output_text"))
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
        reason = str(parsed.get("reason", "")).strip()
        if not reason:
            return response

        parsed.update(
            decision=decision,
            direction="NO SIGNAL" if direction == "NO_SIGNAL" else direction,
            confidence=confidence,
            duration_min=duration,
            reason=reason,
        )
        normalized = dict(body)
        normalized["choices"] = [
            dict(first, message=dict(msg, content=json.dumps(parsed, separators=(",", ":"))))
        ]
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


install()

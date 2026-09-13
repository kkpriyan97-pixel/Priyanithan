from __future__ import annotations

import json
import os
import time
import requests


def _text_from_openai_message(message):
    content = (message or {}).get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content or "")


def _extract_json(text):
    text = str(text or "").strip()
    if not text:
        return None
    cleaned = text.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(cleaned[index:])
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def _normalize_result(result):
    if not isinstance(result, dict):
        return None
    decision = str(result.get("decision", "")).upper().strip()
    direction = str(result.get("direction", "")).upper().strip()
    try:
        confidence = float(result.get("confidence", 0) or 0)
    except Exception:
        confidence = 0.0
    if 0 <= confidence <= 1:
        confidence *= 100
    try:
        expiry = int(float(result.get("expiry", 0) or 0))
    except Exception:
        expiry = 0
    if decision not in ("APPROVE", "REJECT"):
        return None
    if direction not in ("UP", "DOWN", ""):
        return None
    if expiry not in (0, 1, 2, 3, 5, 10, 15):
        return None
    return {
        "decision": decision,
        "direction": direction,
        "confidence": max(0, min(100, int(round(confidence)))),
        "expiry": expiry,
        "reason": str(result.get("reason", "AI decision"))[:500],
    }


def install(app):
    """Install a fail-closed provider chain with automatic Groq free-model rotation.

    Order:
      1) Gemini structured JSON
      2) Every suitable current Groq free/developer model, sequentially
      3) Mistral
      4) Existing Groq/Cerebras primary chain as a final compatibility path

    A provider/model failure never creates an approval. The next model is tried.
    """
    if getattr(app, "_candice_ai_fallback_v6", False):
        return
    import candice_engine as engine
    original_review = engine.ai_review

    def _openai_call(url, key, model, snapshot, memory, strict=False, json_mode=False, timeout=None):
        prompt = engine._ai_prompt(snapshot, memory or {})
        if strict:
            prompt += "\nFINAL OUTPUT RULE: Return exactly one JSON object and nothing else. No markdown, no code fence, no commentary."
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 400,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout or min(float(getattr(engine, "AI_TIMEOUT", 18.0)), 12.0),
        )
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("AI response has no choices")
        content = _text_from_openai_message(choices[0].get("message") or {})
        parsed = _extract_json(content)
        if not parsed:
            raise ValueError("AI returned invalid JSON")
        return parsed

    def _gemini_call(key, model, snapshot, memory, strict=False):
        prompt = engine._ai_prompt(snapshot, memory or {})
        if strict:
            prompt += "\nFINAL OUTPUT RULE: Return exactly one JSON object and nothing else. No markdown, no code fence, no commentary."
        schema = {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["APPROVE", "REJECT"]},
                "direction": {"type": "string", "enum": ["UP", "DOWN", ""]},
                "confidence": {"type": "number"},
                "expiry": {"type": "integer", "enum": [0, 1, 2, 3, 5, 10, 15]},
                "reason": {"type": "string"},
            },
            "required": ["decision", "direction", "confidence", "expiry", "reason"],
        }
        payload = {
            "model": model,
            "input": prompt,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
        }
        response = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=payload,
            timeout=min(float(getattr(engine, "AI_TIMEOUT", 18.0)), 12.0),
        )
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()
        output_text = data.get("output_text")
        if not output_text:
            for step in data.get("steps") or []:
                for item in step.get("content") or []:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        output_text = item["text"]
                        break
                if output_text:
                    break
        parsed = _extract_json(output_text)
        if not parsed:
            raise ValueError("Gemini structured output was empty or invalid")
        return parsed

    def _valid(result):
        return _normalize_result(result)

    def _primary(snapshot, memory):
        last = None
        for attempt in range(2):
            try:
                result = original_review(snapshot, memory)
                normalized = _valid(result)
                last = normalized or result
                reason = str((result or {}).get("reason", "")).lower()
                if normalized and "unavailable" not in reason and "not configured" not in reason:
                    engine.log.info(
                        "AI PRIMARY RECOVERED attempt=%s decision=%s confidence=%s expiry=%s",
                        attempt + 1, normalized.get("decision", ""), normalized.get("confidence", 0), normalized.get("expiry", 0),
                    )
                    return normalized
            except Exception as exc:
                last = {"decision": "REJECT", "confidence": 0, "expiry": 0, "reason": f"primary:{type(exc).__name__}"}
                engine.log.warning("AI primary attempt %s failed: %s", attempt + 1, exc)
            if attempt == 0:
                time.sleep(0.4)
        return last or {"decision": "REJECT", "confidence": 0, "expiry": 0, "reason": "primary unavailable"}

    def _groq_model_list():
        configured = [x.strip() for x in os.getenv("GROQ_MODELS", "").split(",") if x.strip()]
        current_free = [
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "openai/gpt-oss-safeguard-20b",
            "qwen/qwen3.6-27b",
            "qwen/qwen3.8-27b",
            "groq/compound",
            "groq/compound-mini",
        ]
        ordered = []
        for model in configured + [os.getenv("GROQ_MODEL", "").strip()] + current_free:
            if model and model not in ordered:
                ordered.append(model)
        return ordered

    def _groq_rotation(snapshot, memory):
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            return None, ["GROQ:NOT_CONFIGURED"]
        failures = []
        for model in _groq_model_list():
            for attempt in range(1, 3):
                try:
                    out = _openai_call(
                        "https://api.groq.com/openai/v1/chat/completions",
                        key,
                        model,
                        snapshot,
                        memory,
                        strict=(attempt == 2),
                        json_mode=True,
                        timeout=min(float(getattr(engine, "AI_TIMEOUT", 18.0)), 8.0),
                    )
                    normalized = _valid(out)
                    if not normalized:
                        raise ValueError("invalid AI decision schema")
                    engine.log.info(
                        "AI FALLBACK OK provider=GROQ model=%s attempt=%s decision=%s confidence=%s expiry=%s",
                        model, attempt, normalized.get("decision", ""), normalized.get("confidence", 0), normalized.get("expiry", 0),
                    )
                    return normalized, failures
                except Exception as exc:
                    failures.append(f"GROQ:{model}:{type(exc).__name__}")
                    engine.log.warning("AI fallback provider=GROQ model=%s attempt=%s failed: %s", model, attempt, exc)
                    # A quota/rate-limit failure will normally fail immediately; move to the next model.
                    if attempt == 1:
                        continue
                    time.sleep(0.25)
        return None, failures

    def fallback_review(snapshot, memory):
        failures = []

        # First use Gemini because it supports native structured output in the current runtime.
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"
        if gemini_key:
            for attempt in range(2):
                try:
                    out = _gemini_call(gemini_key, gemini_model, snapshot, memory, strict=(attempt == 1))
                    normalized = _valid(out)
                    if not normalized:
                        raise ValueError("invalid AI decision schema")
                    engine.log.info(
                        "AI FALLBACK OK provider=GEMINI attempt=%s model=%s decision=%s confidence=%s expiry=%s",
                        attempt + 1, gemini_model, normalized.get("decision", ""), normalized.get("confidence", 0), normalized.get("expiry", 0),
                    )
                    return normalized
                except Exception as exc:
                    failures.append(f"GEMINI:{type(exc).__name__}")
                    engine.log.warning("AI fallback provider=GEMINI attempt=%s failed: %s", attempt + 1, exc)
                    if attempt == 0:
                        time.sleep(0.5)
        else:
            failures.append("GEMINI:NOT_CONFIGURED")

        # Groq: if one free model is exhausted/broken, automatically try the next available one.
        groq_result, groq_failures = _groq_rotation(snapshot, memory)
        failures.extend(groq_failures)
        if groq_result:
            return groq_result

        mistral_key = os.getenv("MISTRAL_API_KEY", "").strip()
        mistral_model = os.getenv("MISTRAL_MODEL", "mistral-small-latest").strip() or "mistral-small-latest"
        if mistral_key:
            for attempt in range(2):
                try:
                    out = _openai_call(
                        "https://api.mistral.ai/v1/chat/completions",
                        mistral_key,
                        mistral_model,
                        snapshot,
                        memory,
                        strict=(attempt == 1),
                    )
                    normalized = _valid(out)
                    if not normalized:
                        raise ValueError("invalid AI decision schema")
                    engine.log.info(
                        "AI FALLBACK OK provider=MISTRAL attempt=%s model=%s decision=%s confidence=%s expiry=%s",
                        attempt + 1, mistral_model, normalized.get("decision", ""), normalized.get("confidence", 0), normalized.get("expiry", 0),
                    )
                    return normalized
                except Exception as exc:
                    failures.append(f"MISTRAL:{type(exc).__name__}")
                    engine.log.warning("AI fallback provider=MISTRAL attempt=%s failed: %s", attempt + 1, exc)
                    if attempt == 0:
                        time.sleep(0.5)
        else:
            failures.append("MISTRAL:NOT_CONFIGURED")

        primary = _primary(snapshot, memory)
        normalized = _valid(primary)
        reason = str((primary or {}).get("reason", "")).lower()
        if normalized and "unavailable" not in reason and "not configured" not in reason:
            return normalized
        failures.append("PRIMARY:UNAVAILABLE")
        return {
            "decision": "REJECT",
            "direction": "",
            "confidence": 0,
            "expiry": 0,
            "reason": "AI review unavailable — " + ", ".join(failures[:20]),
        }

    engine.ai_review = fallback_review
    app._candice_ai_fallback_v6 = True
    app.log.info("CANDICE AI FALLBACK V6 ACTIVE — GEMINI → ALL GROQ FREE MODELS → MISTRAL → PRIMARY — FAIL CLOSED")

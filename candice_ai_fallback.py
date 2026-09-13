from __future__ import annotations

import os
import time
import requests


def install(app):
    """Install a fail-closed, retrying AI provider chain.

    Chain: existing Groq/Cerebras -> retry -> Gemini -> retry -> Mistral -> reject.
    No provider failure may create an approval.
    """
    if getattr(app, "_candice_ai_fallback_v4", False):
        return

    import candice_engine as engine
    original_review = engine.ai_review

    def _call(url, key, model, snapshot, memory):
        prompt = engine._ai_prompt(snapshot, memory or {})
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 400,
        }
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=min(float(getattr(engine, "AI_TIMEOUT", 18.0)), 12.0),
        )
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("AI response has no choices")
        content = (choices[0].get("message") or {}).get("content", "")
        parsed = engine._parse_ai_content(content)
        if not isinstance(parsed, dict):
            raise ValueError("AI returned invalid JSON")
        return parsed

    def _valid(result):
        if not isinstance(result, dict):
            return False
        decision = str(result.get("decision", "")).upper()
        direction = str(result.get("direction", "")).upper()
        confidence = result.get("confidence", 0)
        expiry = result.get("expiry", 0)
        return decision in ("APPROVE", "REJECT") and direction in ("UP", "DOWN", "") and expiry in (0, 1, 2, 3, 5, 10, 15) and confidence is not None

    def _primary(snapshot, memory):
        last = None
        for attempt in range(2):
            try:
                result = original_review(snapshot, memory)
                last = result
                reason = str((result or {}).get("reason", "")).lower()
                if _valid(result) and "unavailable" not in reason and "not configured" not in reason:
                    engine.log.info("AI PRIMARY RECOVERED attempt=%s decision=%s confidence=%s expiry=%s", attempt + 1, result.get("decision", ""), result.get("confidence", 0), result.get("expiry", 0))
                    return result
            except Exception as exc:
                last = {"decision": "REJECT", "confidence": 0, "reason": f"primary:{type(exc).__name__}"}
                engine.log.warning("AI primary attempt %s failed: %s", attempt + 1, exc)
            if attempt == 0:
                time.sleep(0.4)
        return last or {"decision": "REJECT", "confidence": 0, "reason": "primary unavailable"}

    def fallback_review(snapshot, memory):
        result = _primary(snapshot, memory)
        reason = str((result or {}).get("reason", "")).lower()
        if _valid(result) and "unavailable" not in reason and "not configured" not in reason:
            return result

        providers = (
            ("GEMINI", os.getenv("GEMINI_API_KEY", "").strip(), os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip(), "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
            ("MISTRAL", os.getenv("MISTRAL_API_KEY", "").strip(), os.getenv("MISTRAL_MODEL", "mistral-small-latest").strip(), "https://api.mistral.ai/v1/chat/completions"),
        )
        failures = ["primary-chain-unavailable"]
        for name, key, model, url in providers:
            if not key:
                failures.append(f"{name}:NOT_CONFIGURED")
                continue
            for attempt in range(2):
                try:
                    out = _call(url, key, model, snapshot, memory)
                    if not _valid(out):
                        raise ValueError("invalid AI decision schema")
                    engine.log.info("AI FALLBACK OK provider=%s attempt=%s model=%s decision=%s confidence=%s expiry=%s", name, attempt + 1, model, out.get("decision", ""), out.get("confidence", 0), out.get("expiry", 0))
                    return out
                except Exception as exc:
                    failures.append(f"{name}:{type(exc).__name__}")
                    engine.log.warning("AI fallback provider=%s attempt=%s failed: %s", name, attempt + 1, exc)
                    if attempt == 0:
                        time.sleep(0.5)

        return {
            "decision": "REJECT",
            "confidence": 0,
            "expiry": 0,
            "reason": "AI review unavailable — " + ", ".join(failures[:8]),
        }

    engine.ai_review = fallback_review
    app._candice_ai_fallback_v4 = True
    app.log.info("CANDICE AI FALLBACK V4 ACTIVE — RETRY + GEMINI + MISTRAL — FAIL CLOSED")

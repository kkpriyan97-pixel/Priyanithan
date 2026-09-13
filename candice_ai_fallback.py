from __future__ import annotations

import os
import requests


def install(app):
    """Add reliable Gemini + Mistral fallback after the existing Groq/Cerebras chain."""
    if getattr(app, '_candice_ai_fallback_v2', False):
        return

    import candice_engine as engine
    original_review = engine.ai_review

    def request_compatible(url, key, model, snapshot, memory):
        prompt = engine._ai_prompt(snapshot, memory or {})
        payload = {
            'model': model,
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0.1,
            'max_tokens': 400,
        }
        response = requests.post(
            url,
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            json=payload,
            timeout=min(float(getattr(engine, 'AI_TIMEOUT', 18.0)), 12.0),
        )
        if not response.ok:
            raise RuntimeError(f'HTTP {response.status_code}: {response.text.replace(chr(10), " ")[:500]}')
        choices = response.json().get('choices') or []
        if not choices:
            raise ValueError('AI response has no choices')
        content = (choices[0].get('message') or {}).get('content', '')
        parsed = engine._parse_ai_content(content)
        if not isinstance(parsed, dict):
            raise ValueError('AI returned invalid JSON')
        return parsed

    def fallback_review(snapshot, memory):
        result = original_review(snapshot, memory)
        reason = str((result or {}).get('reason', '')).lower()
        if not ('unavailable' in reason or 'not configured' in reason):
            return result

        providers = (
            ('GEMINI', os.getenv('GEMINI_API_KEY', '').strip(), os.getenv('GEMINI_MODEL', 'gemini-2.5-flash').strip(), 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'),
            ('MISTRAL', os.getenv('MISTRAL_API_KEY', '').strip(), os.getenv('MISTRAL_MODEL', 'mistral-small-latest').strip(), 'https://api.mistral.ai/v1/chat/completions'),
        )
        attempted = False
        for name, key, model, url in providers:
            if not key:
                continue
            attempted = True
            try:
                out = request_compatible(url, key, model, snapshot, memory)
                engine.log.info('AI PROVIDER=%s MODEL=%s DECISION=%s CONFIDENCE=%s EXPIRY=%s', name, model, out.get('decision', ''), out.get('confidence', 0), out.get('expiry', 0))
                return out
            except Exception as exc:
                engine.log.warning('AI fallback provider %s unavailable: %s', name, exc)

        if attempted:
            return {'decision': 'REJECT', 'confidence': 0, 'reason': 'Gemini and Mistral AI review unavailable'}
        return result

    engine.ai_review = fallback_review
    app._candice_ai_fallback_v2 = True
    app.log.info('CANDICE AI FALLBACK V2 ACTIVE — GROQ → CEREBRAS → GEMINI → MISTRAL')

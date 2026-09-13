from __future__ import annotations
import json
import os
import requests


def install(app):
    import candice_engine as engine
    if getattr(app, '_candice_provider_fallback_v2', False):
        return

    original = engine.ai_review

    def request_compatible(url, key, model, snapshot, memory):
        prompt = engine._ai_prompt(snapshot, memory)
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

    def fallback_ai_review(snapshot, memory):
        first = original(snapshot, memory)
        reason = str(first.get('reason', '') or '').lower() if isinstance(first, dict) else ''
        if not any(x in reason for x in ('unavailable', 'not configured')):
            return first

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
                result = request_compatible(url, key, model, snapshot, memory)
                engine.log.info('AI PROVIDER=%s MODEL=%s DECISION=%s CONFIDENCE=%s EXPIRY=%s', name, model, result.get('decision', ''), result.get('confidence', 0), result.get('expiry', 0))
                return result
            except Exception as exc:
                engine.log.warning('AI provider %s unavailable: %s', name, exc)

        if attempted:
            return {'decision': 'REJECT', 'confidence': 0, 'reason': 'Gemini and Mistral AI review unavailable'}
        return first

    engine.ai_review = fallback_ai_review
    app._candice_provider_fallback_v2 = True
    app.log.info('CANDICE AI PROVIDER FALLBACK V2 ACTIVE — GROQ/CEREBRAS -> GEMINI -> MISTRAL')

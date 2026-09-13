from __future__ import annotations

import os
import time
import requests


def install(app):
    """Add resilient Gemini/Mistral fallback after the existing Groq/Cerebras chain.

    Never fabricate an AI approval. If every configured provider fails, return a
    transparent provider-status reason instead of incorrectly naming only
    Gemini/Mistral as unavailable.
    """
    if getattr(app, '_candice_ai_fallback_v3', False):
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
            raise RuntimeError(f'HTTP {response.status_code}: {response.text.replace(chr(10), " ")[:300]}')
        choices = response.json().get('choices') or []
        if not choices:
            raise ValueError('AI response has no choices')
        content = (choices[0].get('message') or {}).get('content', '')
        parsed = engine._parse_ai_content(content)
        if not isinstance(parsed, dict):
            raise ValueError('AI returned invalid JSON')
        return parsed

    def fallback_review(snapshot, memory):
        # First try the normal Groq -> Cerebras chain exactly as the engine defines it.
        result = original_review(snapshot, memory)
        reason = str((result or {}).get('reason', ''))
        low_reason = reason.lower()
        if not ('unavailable' in low_reason or 'not configured' in low_reason):
            return result

        attempted = []
        failures = []

        # One short retry of the primary chain protects against transient provider
        # timeouts/rate limits at a checkpoint without ever generating a fake signal.
        for retry in range(1):
            try:
                retry_result = original_review(snapshot, memory)
                retry_reason = str((retry_result or {}).get('reason', ''))
                if retry_result and not ('unavailable' in retry_reason.lower() or 'not configured' in retry_reason.lower()):
                    engine.log.info('AI RETRY RECOVERED provider-chain decision=%s confidence=%s expiry=%s', retry_result.get('decision', ''), retry_result.get('confidence', 0), retry_result.get('expiry', 0))
                    return retry_result
                failures.append('primary-chain-unavailable')
            except Exception as exc:
                failures.append(f'primary-chain:{type(exc).__name__}')
            time.sleep(0.2)

        providers = (
            ('GEMINI', os.getenv('GEMINI_API_KEY', '').strip(), os.getenv('GEMINI_MODEL', 'gemini-2.5-flash').strip(), 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'),
            ('MISTRAL', os.getenv('MISTRAL_API_KEY', '').strip(), os.getenv('MISTRAL_MODEL', 'mistral-small-latest').strip(), 'https://api.mistral.ai/v1/chat/completions'),
        )
        for name, key, model, url in providers:
            if not key:
                continue
            attempted.append(name)
            try:
                out = request_compatible(url, key, model, snapshot, memory)
                engine.log.info('AI PROVIDER=%s MODEL=%s DECISION=%s CONFIDENCE=%s EXPIRY=%s', name, model, out.get('decision', ''), out.get('confidence', 0), out.get('expiry', 0))
                return out
            except Exception as exc:
                failures.append(f'{name}:{type(exc).__name__}')
                engine.log.warning('AI fallback provider %s unavailable: %s', name, exc)

        configured = ['GROQ/CEREBRAS'] + attempted
        provider_text = ', '.join(configured) if configured else 'none'
        failure_text = ', '.join(failures[:4]) or 'no configured fallback provider'
        return {
            'decision': 'REJECT',
            'confidence': 0,
            'reason': f'AI review unavailable — providers checked: {provider_text}. {failure_text}',
        }

    engine.ai_review = fallback_review
    app._candice_ai_fallback_v3 = True
    app.log.info('CANDICE AI FALLBACK V3 ACTIVE — GROQ → CEREBRAS → GEMINI → MISTRAL + RETRY')

from __future__ import annotations

import os


def install(app):
    """Add Gemini + Mistral fallback after the existing Groq/Cerebras chain."""
    if getattr(app, '_candice_ai_fallback_v1', False):
        return

    import candice_engine as engine

    original_review = engine.ai_review

    def fallback_review(snapshot, memory):
        # Keep the existing primary chain first. Only fall back when both
        # existing providers are genuinely unavailable; a real AI REJECT must
        # remain a REJECT and must not be overridden by another model.
        result = original_review(snapshot, memory)
        reason = str((result or {}).get('reason', '')).lower()
        if not ('groq' in reason and ('cerebras' in reason or 'provider' in reason)):
            return result

        providers = (
            (
                'GEMINI',
                os.getenv('GEMINI_API_KEY', '').strip(),
                os.getenv('GEMINI_MODEL', 'gemini-2.5-flash').strip(),
                'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
            ),
            (
                'MISTRAL',
                os.getenv('MISTRAL_API_KEY', '').strip(),
                os.getenv('MISTRAL_MODEL', 'mistral-small-latest').strip(),
                'https://api.mistral.ai/v1/chat/completions',
            ),
        )
        attempted = False
        for name, key, model, url in providers:
            if not key:
                continue
            attempted = True
            try:
                payload = {'technical': snapshot, 'memory': memory or {}}
                out = engine._request_ai(url, key, model, payload)
                engine.log.info(
                    'AI PROVIDER=%s MODEL=%s DECISION=%s CONFIDENCE=%s EXPIRY=%s',
                    name, model, out.get('decision', ''), out.get('confidence', 0), out.get('expiry', 0)
                )
                return out
            except Exception as exc:
                engine.log.warning('AI fallback provider %s unavailable: %s', name, exc)

        if attempted:
            return {'decision': 'REJECT', 'confidence': 0, 'reason': 'Gemini and Mistral AI review unavailable'}
        return result

    engine.ai_review = fallback_review
    app._candice_ai_fallback_v1 = True
    app.log.info('CANDICE AI FALLBACK ACTIVE — GROQ → CEREBRAS → GEMINI → MISTRAL')

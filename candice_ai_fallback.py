from __future__ import annotations

import json
import os
import time
import requests


def _text_from_openai_message(message):
    content = (message or {}).get('content', '')
    if isinstance(content, str): return content
    if isinstance(content, list):
        return ''.join((item if isinstance(item, str) else str(item.get('text') or item.get('content') or '')) for item in content if isinstance(item, (str, dict)))
    return str(content or '')


def _extract_json(text):
    text = str(text or '').strip().replace('```json', '').replace('```JSON', '').replace('```', '').strip()
    if not text: return None
    try:
        obj = json.loads(text); return obj if isinstance(obj, dict) else None
    except Exception: pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == '{':
            try:
                obj, _ = decoder.raw_decode(text[i:])
                if isinstance(obj, dict): return obj
            except Exception: pass
    return None


def _normalize_result(result):
    if not isinstance(result, dict): return None
    decision = str(result.get('decision', '')).upper().strip()
    direction = str(result.get('direction', '')).upper().strip()
    try: confidence = float(result.get('confidence', 0) or 0)
    except Exception: confidence = 0.0
    if 0 <= confidence <= 1: confidence *= 100
    try: expiry = int(float(result.get('expiry', 0) or 0))
    except Exception: expiry = 0
    if decision not in ('APPROVE', 'REJECT') or direction not in ('UP', 'DOWN', '') or expiry not in (0,1,2,3,5,10,15): return None
    return {'decision':decision,'direction':direction,'confidence':max(0,min(100,int(round(confidence)))),'expiry':expiry,'reason':str(result.get('reason','AI decision'))[:500]}


def _unique_models(*groups):
    out=[]
    for group in groups:
        for model in group:
            model=str(model or '').strip()
            if model and model not in out: out.append(model)
    return out


def _env_models(name): return [x.strip() for x in os.getenv(name,'').split(',') if x.strip()]


def install(app):
    if getattr(app, '_candice_ai_fallback_v8', False): return
    import candice_engine as engine
    original_review=engine.ai_review

    def _openai_call(url,key,model,snapshot,memory,strict=False,json_mode=False,timeout=8.0):
        prompt=engine._ai_prompt(snapshot,memory or {})
        if strict: prompt+='\nFINAL OUTPUT RULE: Return exactly one JSON object and nothing else.'
        payload={'model':model,'messages':[{'role':'user','content':prompt}],'temperature':0.1,'max_tokens':400}
        if json_mode: payload['response_format']={'type':'json_object'}
        r=requests.post(url,headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},json=payload,timeout=timeout)
        if not r.ok: raise RuntimeError(f'HTTP {r.status_code}: {r.text[:300]}')
        choices=r.json().get('choices') or []
        if not choices: raise ValueError('AI response has no choices')
        parsed=_extract_json(_text_from_openai_message(choices[0].get('message') or {}))
        if not parsed: raise ValueError('AI returned invalid JSON')
        return parsed

    def _gemini_models():
        # Do not silently try obsolete Gemini model names. User-supplied GEMINI_MODELS
        # still takes priority; the known-good current default is Flash only.
        return _unique_models(_env_models('GEMINI_MODELS'), [os.getenv('GEMINI_MODEL','').strip()], ['gemini-3.6-flash'])

    def _groq_models(): return _unique_models(_env_models('GROQ_MODELS'),[os.getenv('GROQ_MODEL','').strip()],['openai/gpt-oss-120b','openai/gpt-oss-20b','qwen/qwen3.6-27b','groq/compound-mini'])
    def _mistral_models(): return _unique_models(_env_models('MISTRAL_MODELS'),[os.getenv('MISTRAL_MODEL','').strip()],['mistral-small-latest','mistral-medium-latest','mistral-large-latest','magistral-small-latest'])

    def _gemini_call(key,model,snapshot,memory,strict=False):
        prompt=engine._ai_prompt(snapshot,memory or {})
        if strict: prompt+='\nFINAL OUTPUT RULE: Return exactly one JSON object and nothing else.'
        schema={'type':'object','properties':{'decision':{'type':'string','enum':['APPROVE','REJECT']},'direction':{'type':'string','enum':['UP','DOWN','']},'confidence':{'type':'number'},'expiry':{'type':'integer','enum':[0,1,2,3,5,10,15]},'reason':{'type':'string'}},'required':['decision','direction','confidence','expiry','reason']}
        payload={'model':model,'input':prompt,'response_format':{'type':'text','mime_type':'application/json','schema':schema}}
        r=requests.post('https://generativelanguage.googleapis.com/v1beta/interactions',headers={'x-goog-api-key':key,'Content-Type':'application/json'},json=payload,timeout=12.0)
        if not r.ok: raise RuntimeError(f'HTTP {r.status_code}: {r.text[:300]}')
        data=r.json(); output_text=data.get('output_text')
        if not output_text:
            for step in data.get('steps') or []:
                for item in step.get('content') or []:
                    if isinstance(item,dict) and isinstance(item.get('text'),str): output_text=item['text']; break
                if output_text: break
        parsed=_extract_json(output_text)
        if not parsed: raise ValueError('Gemini structured output was empty or invalid')
        return parsed

    def _rotate_openai(provider,url,key,models,snapshot,memory):
        failures=[]
        if not key: return None,[f'{provider}:NOT_CONFIGURED']
        for model in models:
            for attempt in (1,2):
                try:
                    out=_openai_call(url,key,model,snapshot,memory,strict=(attempt==2),json_mode=(provider=='GROQ'))
                    normalized=_normalize_result(out)
                    if not normalized: raise ValueError('invalid AI decision schema')
                    engine.log.info('AI FALLBACK OK provider=%s model=%s attempt=%s decision=%s confidence=%s expiry=%s',provider,model,attempt,normalized['decision'],normalized['confidence'],normalized['expiry'])
                    return normalized,failures
                except Exception as exc:
                    failures.append(f'{provider}:{model}:{type(exc).__name__}')
                    engine.log.warning('AI fallback provider=%s model=%s attempt=%s failed: %s',provider,model,attempt,exc)
                    msg=str(exc)
                    if 'HTTP 429' in msg or 'HTTP 404' in msg: break
                    if attempt==1: time.sleep(0.25)
        return None,failures

    def _rotate_gemini(key,models,snapshot,memory):
        failures=[]
        if not key: return None,['GEMINI:NOT_CONFIGURED']
        for model in models:
            for attempt in (1,2):
                try:
                    out=_gemini_call(key,model,snapshot,memory,strict=(attempt==2))
                    normalized=_normalize_result(out)
                    if not normalized: raise ValueError('invalid AI decision schema')
                    engine.log.info('AI FALLBACK OK provider=GEMINI model=%s attempt=%s decision=%s confidence=%s expiry=%s',model,attempt,normalized['decision'],normalized['confidence'],normalized['expiry'])
                    return normalized,failures
                except Exception as exc:
                    failures.append(f'GEMINI:{model}:{type(exc).__name__}')
                    engine.log.warning('AI fallback provider=GEMINI model=%s attempt=%s failed: %s',model,attempt,exc)
                    msg=str(exc)
                    if 'HTTP 429' in msg or 'HTTP 404' in msg: break
                    if attempt==1: time.sleep(0.25)
        return None,failures

    def _primary(snapshot,memory):
        try:
            result=original_review(snapshot,memory); normalized=_normalize_result(result); reason=str((result or {}).get('reason','')).lower()
            if normalized and 'unavailable' not in reason and 'not configured' not in reason: return normalized
        except Exception as exc: engine.log.warning('AI primary failed: %s',exc)
        return None

    def fallback_review(snapshot,memory):
        failures=[]
        result,errs=_rotate_gemini(os.getenv('GEMINI_API_KEY','').strip(),_gemini_models(),snapshot,memory); failures.extend(errs)
        if result:return result
        result,errs=_rotate_openai('GROQ','https://api.groq.com/openai/v1/chat/completions',os.getenv('GROQ_API_KEY','').strip(),_groq_models(),snapshot,memory); failures.extend(errs)
        if result:return result
        result,errs=_rotate_openai('MISTRAL','https://api.mistral.ai/v1/chat/completions',os.getenv('MISTRAL_API_KEY','').strip(),_mistral_models(),snapshot,memory); failures.extend(errs)
        if result:return result
        primary=_primary(snapshot,memory)
        if primary:return primary
        failures.append('PRIMARY:UNAVAILABLE')
        return {'decision':'REJECT','direction':'','confidence':0,'expiry':0,'reason':'AI review unavailable — '+', '.join(failures[:30])}

    engine.ai_review=fallback_review
    app._candice_ai_fallback_v8=True
    app.log.info('CANDICE AI FALLBACK V8 ACTIVE — Gemini invalid/quota models skipped fast; Groq → Mistral → primary; FAIL CLOSED')

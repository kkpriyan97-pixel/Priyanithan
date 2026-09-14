from __future__ import annotations
import json, os, requests


def _extract_json(text):
    text=str(text or '').strip().replace('```json','').replace('```JSON','').replace('```','').strip()
    if not text: return None
    try:
        obj=json.loads(text); return obj if isinstance(obj,dict) else None
    except Exception: pass
    a,b=text.find('{'),text.rfind('}')
    if a>=0 and b>a:
        try:
            obj=json.loads(text[a:b+1]); return obj if isinstance(obj,dict) else None
        except Exception: pass
    return None


def _text(message):
    c=(message or {}).get('content','')
    if isinstance(c,str): return c
    if isinstance(c,list):
        return ''.join(x if isinstance(x,str) else str((x or {}).get('text') or (x or {}).get('content') or '') for x in c if isinstance(x,(str,dict)))
    return str(c or '')


def _normalize(x):
    if not isinstance(x,dict): return None
    d=str(x.get('decision','')).upper().strip(); direction=str(x.get('direction','')).upper().strip()
    try: conf=float(x.get('confidence',0) or 0)
    except Exception: conf=0.0
    if 0<=conf<=1: conf*=100
    try: exp=int(float(x.get('expiry',0) or 0))
    except Exception: exp=0
    if d not in ('APPROVE','REJECT') or direction not in ('UP','DOWN','') or exp not in (0,1,2,3,5,10,15): return None
    return {'decision':d,'direction':direction,'confidence':max(0,min(100,int(round(conf)))),'expiry':exp,'reason':str(x.get('reason','AI decision'))[:500]}


def _models(models_env,model_env,defaults):
    out=[]
    for x in [v.strip() for v in os.getenv(models_env,'').split(',') if v.strip()]+[os.getenv(model_env,'').strip()]+defaults:
        if x and x not in out: out.append(x)
    return out


def install(app):
    if getattr(app,'_candice_ai_fallback_v9',False): return
    import candice_engine as engine
    original_review=engine.ai_review

    def openai_call(url,key,model,snapshot,memory):
        prompt=engine._ai_prompt(snapshot,memory or {})+'\nReturn ONLY one JSON object with keys decision,direction,confidence,expiry,reason. No markdown.'
        payload={'model':model,'messages':[{'role':'user','content':prompt}],'temperature':0.1,'max_tokens':300}
        r=requests.post(url,headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},json=payload,timeout=7)
        if not r.ok: raise RuntimeError(f'HTTP {r.status_code}: {r.text[:220]}')
        choices=r.json().get('choices') or []
        if not choices: raise ValueError('AI response has no choices')
        out=_normalize(_extract_json(_text(choices[0].get('message') or {})))
        if not out: raise ValueError('AI response is not valid decision JSON')
        return out

    def gemini_call(key,model,snapshot,memory):
        prompt=engine._ai_prompt(snapshot,memory or {})+'\nReturn ONLY one JSON object with keys decision,direction,confidence,expiry,reason. No markdown.'
        # Avoid provider-side structured-output schema because it has caused incompatibility on some current endpoints/models.
        payload={'model':model,'input':prompt}
        r=requests.post('https://generativelanguage.googleapis.com/v1beta/interactions',headers={'x-goog-api-key':key,'Content-Type':'application/json'},json=payload,timeout=8)
        if not r.ok: raise RuntimeError(f'HTTP {r.status_code}: {r.text[:220]}')
        data=r.json(); txt=data.get('output_text','')
        if not txt:
            for step in data.get('steps') or []:
                for item in step.get('content') or []:
                    if isinstance(item,dict) and isinstance(item.get('text'),str): txt=item['text']; break
                if txt: break
        out=_normalize(_extract_json(txt))
        if not out: raise ValueError('Gemini response is not valid decision JSON')
        return out

    def rotate(name,key,models,call):
        failures=[]
        if not key: return None,[f'{name}:NOT_CONFIGURED']
        for model in models:
            try:
                out=call(key,model)
                engine.log.info('AI FALLBACK OK provider=%s model=%s decision=%s confidence=%s expiry=%s',name,model,out['decision'],out['confidence'],out['expiry'])
                return out,failures
            except Exception as exc:
                msg=str(exc); failures.append(f'{name}:{model}:{type(exc).__name__}')
                engine.log.warning('AI fallback provider=%s model=%s failed: %s',name,model,msg)
                # Never retry the same model for quota, unavailable-model, or subscription errors.
                continue
        return None,failures

    def fallback_review(snapshot,memory):
        failures=[]
        result,errs=rotate('GEMINI',os.getenv('GEMINI_API_KEY','').strip(),_models('GEMINI_MODELS','GEMINI_MODEL',['gemini-3.6-flash']),lambda k,m:gemini_call(k,m,snapshot,memory)); failures+=errs
        if result:return result
        result,errs=rotate('GROQ',os.getenv('GROQ_API_KEY','').strip(),_models('GROQ_MODELS','GROQ_MODEL',['openai/gpt-oss-120b','openai/gpt-oss-20b','qwen/qwen3.6-27b','groq/compound-mini']),lambda k,m:openai_call('https://api.groq.com/openai/v1/chat/completions',k,m,snapshot,memory)); failures+=errs
        if result:return result
        result,errs=rotate('MISTRAL',os.getenv('MISTRAL_API_KEY','').strip(),_models('MISTRAL_MODELS','MISTRAL_MODEL',['mistral-small-latest','mistral-medium-latest','magistral-small-latest']),lambda k,m:openai_call('https://api.mistral.ai/v1/chat/completions',k,m,snapshot,memory)); failures+=errs
        if result:return result
        try:
            out=_normalize(original_review(snapshot,memory))
            if out:return out
        except Exception as exc: failures.append(f'PRIMARY:{type(exc).__name__}')
        return {'decision':'REJECT','direction':'','confidence':0,'expiry':0,'reason':'AI review unavailable — '+', '.join(failures[:20])}

    engine.ai_review=fallback_review
    app._candice_ai_fallback_v9=True
    app.log.info('CANDICE AI FALLBACK V9 ACTIVE — schema-compatible rotation; no forced JSON mode; 404/403/429 skip; FAIL CLOSED')

from __future__ import annotations
import os
import requests


def _extract_json(text):
    import json
    text = str(text or "").strip().replace("```json", "").replace("```", "").strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        decoder = json.JSONDecoder()
        for i, ch in enumerate(text):
            if ch == "{":
                try:
                    obj, _ = decoder.raw_decode(text[i:])
                    if isinstance(obj, dict):
                        return obj
                except Exception:
                    pass
    return None


def install(app):
    if getattr(app, "_candice_zai_v1", False):
        return
    import candice_engine as engine
    previous = engine.ai_review

    def review(snapshot, memory):
        key = os.getenv("ZAI_API_KEY", "").strip()
        if not key or key == "PLEASE_SET_YOUR_ZAI_API_KEY":
            return previous(snapshot, memory)
        models = [x.strip() for x in os.getenv("ZAI_MODELS", "glm-4.7-flash,glm-4.5-flash").split(",") if x.strip()]
        prompt = engine._ai_prompt(snapshot, memory or {}) + "\nReturn exactly one JSON object: decision, direction, confidence, expiry, reason."
        for model in models:
            for attempt in range(2):
                try:
                    r = requests.post(
                        "https://api.z.ai/api/paas/v4/chat/completions",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={"model": model, "messages":[{"role":"user","content":prompt}],"temperature":0.1,"max_tokens":400,"response_format":{"type":"json_object"}},
                        timeout=min(float(getattr(engine, "AI_TIMEOUT", 18.0)), 10.0),
                    )
                    if not r.ok:
                        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:250]}")
                    data = r.json()
                    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
                    if isinstance(content, list):
                        content = "".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in content)
                    out = _extract_json(content)
                    if not isinstance(out, dict):
                        raise ValueError("invalid Z.ai JSON")
                    decision = str(out.get("decision", "")).upper().strip()
                    direction = str(out.get("direction", "")).upper().strip()
                    confidence = float(out.get("confidence", 0) or 0)
                    if 0 <= confidence <= 1:
                        confidence *= 100
                    expiry = int(float(out.get("expiry", 0) or 0))
                    if decision not in ("APPROVE", "REJECT") or direction not in ("UP", "DOWN", "") or expiry not in (0,1,2,3,5,10,15):
                        raise ValueError("invalid Z.ai decision schema")
                    result = {"decision":decision,"direction":direction,"confidence":max(0,min(100,int(round(confidence)))),"expiry":expiry,"reason":str(out.get("reason","Z.ai decision"))[:500]}
                    engine.log.info("AI ZAI OK model=%s attempt=%s decision=%s confidence=%s expiry=%s", model, attempt+1, result["decision"], result["confidence"], result["expiry"])
                    return result
                except Exception as exc:
                    engine.log.warning("AI ZAI model=%s attempt=%s failed: %s", model, attempt+1, exc)
        return previous(snapshot, memory)

    engine.ai_review = review
    app._candice_zai_v1 = True
    app.log.info("CANDICE Z.AI GLM FLASH ROTATION ACTIVE — FAILOVER")

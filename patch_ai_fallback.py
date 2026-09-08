from pathlib import Path

p = Path('app.py')
s = p.read_text(encoding='utf-8')
start = s.index('def call_ai(prompt):')
marker = '\n# ============================================================\n# AI DECISION SAFETY'
end = s.index(marker, start)
new = '''def call_ai(prompt):
    """Validate a setup with independent AI providers and fail over automatically."""
    log.info("AI FALLBACK CHAIN START: prompt_chars=%s", len(str(prompt)))
    providers = []

    if OPENROUTER_API_KEY:
        models = []
        for m in (OPENROUTER_MODEL, "openrouter/free", "minimax/minimax-m3:free", "google/gemma-4-26b-a4b-it:free"):
            if m and m not in models:
                models.append(m)
        for m in models:
            providers.append((f"OpenRouter/{m}", "https://openrouter.ai/api/v1/chat/completions", OPENROUTER_API_KEY, m))

    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        providers.append(("Groq", "https://api.groq.com/openai/v1/chat/completions", groq_key, os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")))

    cerebras_key = os.getenv("CEREBRAS_API_KEY")
    if cerebras_key:
        providers.append(("Cerebras", "https://api.cerebras.ai/v1/chat/completions", cerebras_key, os.getenv("CEREBRAS_MODEL", "llama3.1-8b")))

    mistral_key = os.getenv("MISTRAL_API_KEY")
    if mistral_key:
        providers.append(("Mistral", "https://api.mistral.ai/v1/chat/completions", mistral_key, os.getenv("MISTRAL_MODEL", "mistral-small-latest")))

    if AIRFORCE_API_KEY:
        providers.append(("Airforce", "https://api.airforce/v1/chat/completions", AIRFORCE_API_KEY, AIRFORCE_MODEL))

    if not providers:
        log.error("AI FALLBACK CHAIN SKIPPED: no AI provider configured")
        return None, "No AI provider configured"

    last_error = None
    for index, (name, url, key, model) in enumerate(providers, start=1):
        try:
            log.info("AI PROVIDER TRY %s/%s: %s model=%s", index, len(providers), name, model)
            headers = {"Authorization": f"Bearer {key.strip()}", "Content-Type": "application/json"}
            if name.startswith("OpenRouter/"):
                headers["HTTP-Referer"] = "https://priyanithan-ai.onrender.com"
                headers["X-Title"] = "Priyanithan AI OlympTrade Signal Bot"
            payload = {"model": model, "messages": [{"role":"system","content":"Return JSON only."},{"role":"user","content":prompt}], "temperature":0, "max_tokens":300}
            started = time.time()
            r = requests.post(url, headers=headers, json=payload, timeout=15)
            elapsed = time.time() - started
            log.info("AI PROVIDER RESPONSE: %s model=%s status=%s elapsed=%.2fs bytes=%s", name, model, r.status_code, elapsed, len(r.content))
            if not r.ok:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500].replace(chr(10), ' ')}")
            parsed = parse_ai(r.json())
            log.info("AI PROVIDER SUCCESS: %s model=%s decision=%s direction=%s confidence=%s", name, model, parsed.get("decision"), parsed.get("direction"), parsed.get("confidence"))
            return parsed, None
        except Exception as e:
            last_error = f"{name}: {e}"
            log.warning("AI PROVIDER FAILED: %s", last_error)
            if index < len(providers):
                log.warning("AI FAILOVER: switching from %s to %s", name, providers[index][0])

    log.error("AI FALLBACK CHAIN EXHAUSTED: %s", last_error or "unknown error")
    return None, last_error or "All AI providers failed"
'''
s = s[:start] + new + s[end:]
p.write_text(s, encoding='utf-8')
print('AI fallback patch applied')
# trigger marker

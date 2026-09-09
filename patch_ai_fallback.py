from pathlib import Path

p = Path('app.py')
s = p.read_text(encoding='utf-8')

# ------------------------------------------------------------------
# Harden the AI response parser.
# ------------------------------------------------------------------
start = s.index('def parse_ai(data):')
marker = '\ndef call_ai(prompt):'
end = s.index(marker, start)
new_parse = r'''def parse_ai(data):
    """Parse OpenAI-compatible responses and find the actual decision JSON."""
    if not isinstance(data, dict):
        raise ValueError("AI response is not a JSON object")
    choices = data.get("choices")
    if not choices or not isinstance(choices[0], dict):
        raise ValueError(f"AI response missing choices: {str(data)[:500]}")
    choice = choices[0]
    msg = choice.get("message") if isinstance(choice.get("message"), dict) else {}

    values = [
        msg.get("content"),
        choice.get("text"),
        data.get("output_text"),
        data.get("response"),
        data.get("result"),
        msg.get("reasoning_content"),
        msg.get("reasoning"),
    ]
    candidates = []
    for value in values:
        if isinstance(value, list):
            value = "".join(
                str(x.get("text") or x.get("content") or "") if isinstance(x, dict) else str(x)
                for x in value
            )
        if value:
            candidates.append(value if isinstance(value, dict) else str(value))

    for value in candidates:
        if isinstance(value, dict) and "decision" in value:
            return value
        if not isinstance(value, str):
            continue
        text = value.strip()
        text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```\s*$", "", text)
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and "decision" in obj:
                return obj
        except (json.JSONDecodeError, TypeError):
            pass
        for match in re.finditer(r'\{', text):
            try:
                obj, _ = json.JSONDecoder().raw_decode(text[match.start():])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(obj, dict) and "decision" in obj:
                return obj

    raise ValueError("AI response contained no valid decision JSON")
'''
s = s[:start] + new_parse + s[end:]

# ------------------------------------------------------------------
# Replace the provider caller with provider-aware structured output.
# Groq GPT-OSS supports strict JSON Schema; if a provider rejects that
# format, retry once with JSON-object mode before failing over.
# ------------------------------------------------------------------
start = s.index('def call_ai(prompt):')
marker = '\n# ============================================================\n# AI DECISION SAFETY'
end = s.index(marker, start)
new_call = r'''def call_ai(prompt):
    """Validate a setup with independent AI providers and robust failover."""
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

    schema = {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["APPROVE", "REJECT"]},
            "direction": {"type": "string", "enum": ["UP", "DOWN", "NO SIGNAL"]},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "duration_min": {"type": "integer", "enum": [1, 2, 3, 5, 10, 15]},
            "reason": {"type": "string"},
        },
        "required": ["decision", "direction", "confidence", "duration_min", "reason"],
        "additionalProperties": False,
    }

    last_error = None
    for index, (name, url, key, model) in enumerate(providers, start=1):
        try:
            log.info("AI PROVIDER TRY %s/%s: %s model=%s", index, len(providers), name, model)
            headers = {"Authorization": f"Bearer {key.strip()}", "Content-Type": "application/json"}
            if name.startswith("OpenRouter/"):
                headers["HTTP-Referer"] = "https://priyanithan-ai.onrender.com"
                headers["X-Title"] = "Priyanithan AI OlympTrade Signal Bot"

            is_groq = name == "Groq"
            if is_groq:
                messages = [{"role": "user", "content": str(prompt) + "\n\nReturn exactly one JSON object matching the required fields. No markdown and no extra text."}]
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0,
                    "max_completion_tokens": 512,
                    "reasoning_effort": "low",
                    "include_reasoning": False,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "trading_signal_decision",
                            "strict": True,
                            "schema": schema,
                        },
                    },
                }
            else:
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "Return exactly one valid JSON object and no reasoning/prose/markdown."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0,
                    "max_tokens": 300,
                    "response_format": {"type": "json_object"},
                }

            started = time.time()
            r = requests.post(url, headers=headers, json=payload, timeout=20)
            elapsed = time.time() - started
            log.info("AI PROVIDER RESPONSE: %s model=%s status=%s elapsed=%.2fs bytes=%s", name, model, r.status_code, elapsed, len(r.content))

            # Groq JSON-schema validation can reject a generation. Retry once in
            # plain JSON-object mode before moving to the next independent provider.
            if is_groq and r.status_code == 400:
                log.warning("GROQ STRUCTURED OUTPUT RETRY: switching to JSON object mode")
                retry_payload = dict(payload)
                retry_payload["response_format"] = {"type": "json_object"}
                retry_payload.pop("reasoning_effort", None)
                retry_payload.pop("include_reasoning", None)
                retry_payload["max_completion_tokens"] = 768
                retry = requests.post(url, headers=headers, json=retry_payload, timeout=20)
                log.info("AI PROVIDER RETRY RESPONSE: %s model=%s status=%s bytes=%s", name, model, retry.status_code, len(retry.content))
                r = retry

            if not r.ok:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500].replace(chr(10), ' ')}")
            try:
                body = r.json()
            except Exception as e:
                raise RuntimeError(f"invalid provider JSON envelope: {e}; body={r.text[:300]}")
            parsed = parse_ai(body)
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
s = s[:start] + new_call + s[end:]

# Prevent accidental attempts to message the bot's own Telegram account.
start = s.index('async def send_to_recipients(bot, text):')
marker = '\n# ============================================================\n# CANDLE NORMALIZATION'
end = s.index(marker, start)
new_send = r'''async def send_to_recipients(bot, text):
    ids = recipients()
    if not ids:
        log.warning("AUTO SCAN: no Telegram recipient. Use /access YOUR_CODE once, or set TELEGRAM_CHAT_ID in Render.")
        return False
    try:
        bot_id = int((await bot.get_me()).id)
    except Exception:
        bot_id = None
    sent = False
    for chat_id in ids:
        if bot_id is not None and int(chat_id) == bot_id:
            log.warning("Telegram recipient %s is the bot's own ID; skipped", chat_id)
            continue
        try:
            await bot.send_message(chat_id=chat_id, text=text)
            sent = True
        except Exception as e:
            log.warning("Telegram send failed chat_id=%s: %s", chat_id, e)
    return sent
'''
s = s[:start] + new_send + s[end:]

p.write_text(s, encoding='utf-8')
print('Applied robust Groq structured-output fallback + AI parser + Telegram guard')

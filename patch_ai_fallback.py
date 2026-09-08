from pathlib import Path

p = Path('app.py')
s = p.read_text(encoding='utf-8')

# Replace parse_ai with a robust OpenAI-compatible response parser.
start = s.index('def parse_ai(data):')
marker = '\ndef call_ai(prompt):'
end = s.index(marker, start)
new_parse = r'''def parse_ai(data):
    """Parse OpenAI-compatible AI output, including JSON in code fences/text."""
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        raise ValueError(f"AI response missing choices: {str(data)[:400]}")
    choice = choices[0] if isinstance(choices[0], dict) else {}
    msg = choice.get("message", {}) if isinstance(choice, dict) else {}
    content = msg.get("content") or choice.get("text") or msg.get("reasoning_content") or msg.get("reasoning")

    # Some providers return content as structured blocks.
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        content = "".join(parts)

    # Some OpenAI-compatible providers put useful JSON in another field.
    if not content:
        for key in ("output_text", "response", "result", "content"):
            value = data.get(key) if isinstance(data, dict) else None
            if value:
                content = value
                break

    content = str(content or "").strip()
    if not content:
        raise ValueError("AI response contained no text content")

    # Strip markdown fences and locate the first JSON object if the model added prose.
    content = re.sub(r"^\s*```(?:json)?\s*", "", content, flags=re.I)
    content = re.sub(r"\s*```\s*$", "", content)
    if not content.startswith("{"):
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            content = m.group(0)

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        # Tolerate a single JSON object embedded between surrounding text.
        decoder = json.JSONDecoder()
        match = re.search(r"\{", content)
        if not match:
            raise
        result, _ = decoder.raw_decode(content[match.start():])

    if not isinstance(result, dict):
        raise ValueError("AI JSON result is not an object")
    return result
'''
s = s[:start] + new_parse + s[end:]

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
print('Robust AI parser + Telegram self-recipient guard applied')

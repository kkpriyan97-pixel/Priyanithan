from pathlib import Path

p = Path('app.py')
s = p.read_text(encoding='utf-8')

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

    values = [msg.get("content"), choice.get("text"), data.get("output_text"), data.get("response"), data.get("result"), msg.get("reasoning_content"), msg.get("reasoning")]
    candidates = []
    for value in values:
        if isinstance(value, list):
            value = "".join(str(x.get("text") or x.get("content") or "") if isinstance(x, dict) else str(x) for x in value)
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
        # Reasoning may contain several {...} fragments; only accept one with decision.
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
print('Hardened AI decision parser + Telegram self-recipient guard applied')

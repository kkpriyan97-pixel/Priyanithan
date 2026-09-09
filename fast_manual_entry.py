"""Priyanithan runtime flow + live AI decision bridge.

START -> ACCESS -> DEMO/REAL -> AI/technical scan -> qualified signal ->
Telegram Trade Now page -> official Olymp Trade web. No auto-order execution.
"""
import asyncio
import hashlib
import hmac
import html
import json
import os
import re
import sys
import threading
import time
from urllib.parse import urlencode

import requests
from flask import request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

os.environ.setdefault("RENDER_EXTERNAL_URL", "https://priyanithan.onrender.com")

BASE = "https://priyanithan.onrender.com"
DEMO_URL = "https://olymptrade.com/pages/trading/account/free-demo/"
REAL_URL = "https://olymptrade.com/pages/trading/"
EXPIRIES = (1, 2, 3, 5, 10, 15)
MODES = {}
LOCK = threading.Lock()

SIGNAL_RE = re.compile(
    r"🔥?\s*PRIYANITHAN AI SIGNAL\s*🔥?.*?"
    r"📈\s*([^\n]+).*?(⬆️\s*UP|⬇️\s*DOWN).*?"
    r"💰\s*Entry:\s*([^\n]+).*?⏱️\s*Expiry:\s*(\d+)\s*MIN",
    re.S | re.I,
)


def app():
    m = sys.modules.get("__main__")
    return m if m is not None and getattr(m, "__file__", "").endswith("app.py") else sys.modules.get("app")


def secret():
    return (os.getenv("ACCESS_CODE") or os.getenv("TELEGRAM_BOT_TOKEN") or "priyanithan-final").encode()


def sign(value):
    return hmac.new(secret(), value.encode(), hashlib.sha256).hexdigest()[:24]


def pack(value):
    return value + "." + sign(value)


def unpack(value):
    try:
        raw, sig = str(value).rsplit(".", 1)
    except ValueError:
        return None
    return raw if hmac.compare_digest(sig, sign(raw)) else None


def mode(cid):
    with LOCK:
        return MODES.get(int(cid))


def set_mode(cid, value):
    with LOCK:
        MODES[int(cid)] = value


def authorized_ids(a):
    try:
        return {int(x) for x in a.authorized_users}
    except Exception:
        return set()


def parse_signal(text):
    m = SIGNAL_RE.search(str(text or ""))
    if not m:
        return None
    try:
        expiry = int(m.group(4))
    except Exception:
        return None
    pair = m.group(1).strip()
    direction = "UP" if "UP" in m.group(2).upper() else "DOWN"
    entry = m.group(3).strip()
    return (pair, direction, entry, expiry) if pair and entry and expiry in EXPIRIES else None


def base_url():
    return os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/") or BASE


def mode_url(cid, value):
    raw = f"{int(cid)}|{value}|{int(time.time()) // 600}"
    return base_url() + "/trade/mode?" + urlencode({"token": pack(raw)})


def trade_app_url(cid, pair, direction, entry, expiry):
    raw = f"{int(cid)}|{pair}|{direction}|{entry}|{expiry}|{int(time.time()) // 300}"
    return base_url() + "/trade/app?" + urlencode({"token": pack(raw)})


def mode_markup(cid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🧪 DEMO", url=mode_url(cid, "DEMO")),
        InlineKeyboardButton("🔴 REAL", url=mode_url(cid, "REAL")),
    ]])


def signal_markup(cid, text):
    data = parse_signal(text)
    if not data or mode(cid) not in ("DEMO", "REAL"):
        return None
    pair, direction, entry, expiry = data
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "⚡ TRADE NOW",
            web_app=WebAppInfo(url=trade_app_url(cid, pair, direction, entry, expiry)),
        )
    ]])


def patch_ai(a):
    """Install a real OpenAI-compatible AI caller into app.py's scan path."""
    if getattr(a, "_LIVE_AI_PATCH", False):
        return True

    def parse_ai(body):
        if not isinstance(body, dict):
            raise ValueError("AI response is not an object")
        choices = body.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise ValueError("AI response has no choices")
        choice = choices[0]
        msg = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        values = [msg.get("content"), choice.get("text"), body.get("output_text")]
        for value in values:
            if isinstance(value, list):
                value = "".join(
                    str(x.get("text") or x.get("content") or "") if isinstance(x, dict) else str(x)
                    for x in value
                )
            if isinstance(value, dict) and "decision" in value:
                return value
            if not value:
                continue
            text = str(value).strip()
            text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```\s*$", "", text)
            try:
                obj = json.loads(text)
                if isinstance(obj, dict) and "decision" in obj:
                    return obj
            except Exception:
                pass
            for match in re.finditer(r"\{", text):
                try:
                    obj, _ = json.JSONDecoder().raw_decode(text[match.start():])
                    if isinstance(obj, dict) and "decision" in obj:
                        return obj
                except Exception:
                    continue
        raise ValueError("AI response contained no decision JSON")

    schema = {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["APPROVE", "REJECT"]},
            "direction": {"type": "string", "enum": ["UP", "DOWN", "NO SIGNAL"]},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "duration_min": {"type": "integer", "enum": list(EXPIRIES)},
            "reason": {"type": "string"},
        },
        "required": ["decision", "direction", "confidence", "duration_min", "reason"],
        "additionalProperties": False,
    }

    providers = []
    or_key = os.getenv("OPENROUTER_API_KEY")
    if or_key:
        providers.append(("OpenRouter", "https://openrouter.ai/api/v1/chat/completions", or_key, os.getenv("OPENROUTER_MODEL", "openrouter/free")))
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        providers.append(("Groq", "https://api.groq.com/openai/v1/chat/completions", groq_key, os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")))
    air_key = os.getenv("AIRFORCE_API_KEY")
    if air_key:
        providers.append(("Airforce", "https://api.airforce/v1/chat/completions", air_key, os.getenv("AIRFORCE_MODEL", "gpt-oss-120b")))

    def call_ai(prompt):
        if not providers:
            a.log.error("AI PROVIDER UNAVAILABLE: configure OPENROUTER_API_KEY, GROQ_API_KEY, or AIRFORCE_API_KEY")
            return None, "No AI provider configured"
        system = (
            "You are Priyanithan professional trading-analysis AI. "
            "Analyze ONLY the supplied live technical snapshot. Do not invent data. "
            "Approve only when trend, momentum, RSI/Stochastic and price structure agree. "
            "Reject mixed, weak, overextended or uncertain setups. "
            "Return one JSON object only with decision, direction, confidence, duration_min and reason."
        )
        last_error = None
        for name, url, key, model in providers:
            try:
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": str(prompt) + "\n\nRequired schema:\n" + json.dumps(schema)},
                    ],
                    "temperature": 0,
                    "max_tokens": 350,
                }
                headers = {"Authorization": f"Bearer {key.strip()}", "Content-Type": "application/json"}
                if name == "OpenRouter":
                    headers["HTTP-Referer"] = base_url()
                    headers["X-Title"] = "Priyanithan AI Signal Bot"
                started = time.time()
                r = requests.post(url, headers=headers, json=payload, timeout=20)
                a.log.info("AI PROVIDER RESPONSE: %s model=%s status=%s elapsed=%.2fs", name, model, r.status_code, time.time() - started)
                if not r.ok:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:400]}")
                parsed = parse_ai(r.json())
                decision = str(parsed.get("decision", "")).upper()
                direction = str(parsed.get("direction", "")).upper()
                confidence = int(parsed.get("confidence", 0))
                duration = int(parsed.get("duration_min", 5))
                if decision not in ("APPROVE", "REJECT") or direction not in ("UP", "DOWN", "NO SIGNAL"):
                    raise ValueError("invalid AI decision fields")
                if not 0 <= confidence <= 100 or duration not in EXPIRIES:
                    raise ValueError("invalid AI confidence/duration")
                parsed["decision"] = decision
                parsed["direction"] = direction
                parsed["confidence"] = confidence
                parsed["duration_min"] = duration
                a.log.info("AI DECISION: provider=%s decision=%s direction=%s confidence=%s duration=%s reason=%s",
                           name, decision, direction, confidence, duration, str(parsed.get("reason", ""))[:180])
                return parsed, None
            except Exception as exc:
                last_error = f"{name}: {exc}"
                a.log.warning("AI PROVIDER FAILED: %s", last_error)
        return None, last_error or "AI providers failed"

    a.call_ai = call_ai
    a._LIVE_AI_PATCH = True
    a.log.warning("LIVE AI PATCH ACTIVE: scan_cycle now uses configured AI provider(s)")
    return True


def start_scan(cid):
    a = app()
    loop = getattr(a, "runtime_loop", None) if a else None
    application = getattr(a, "telegram_application", None) if a else None
    if not a or not loop or not application or mode(cid) not in ("DEMO", "REAL"):
        return False
    current = getattr(a, "manual_scan_task", None)
    if current is not None and not current.done():
        return True

    def kick():
        current = getattr(a, "manual_scan_task", None)
        if current is None or current.done():
            try:
                a.manual_scan_task = asyncio.create_task(a.scan_cycle(application), name="manual-scan")
            except Exception as exc:
                a.log.warning("FINAL FLOW scan start failed: %s", exc)

    loop.call_soon_threadsafe(kick)
    return True


def patch_start(a):
    application = getattr(a, "telegram_application", None)
    if not application:
        return False
    for handlers in getattr(application, "handlers", {}).values():
        for handler in handlers:
            if "start" not in getattr(handler, "commands", set()):
                continue
            if getattr(handler.callback, "_FINAL_START", False):
                return True

            async def final_start(update, context):
                user = getattr(update, "effective_user", None)
                chat = getattr(update, "effective_chat", None)
                uid = getattr(user, "id", getattr(chat, "id", None))
                cid = getattr(chat, "id", None)
                if cid is None:
                    return
                try:
                    a.remember_chat(update)
                except Exception:
                    pass
                if uid not in authorized_ids(a):
                    await update.message.reply_text("🔐 ACCESS REQUIRED\n\nSend /access YOUR_ACCESS_CODE first.")
                    return
                if mode(uid) not in ("DEMO", "REAL"):
                    await update.message.reply_text(
                        "🎯 PRIYANITHAN AI TRADING\n\nACCESS: VERIFIED ✅\n"
                        "Choose DEMO or REAL before the scan.\n"
                        "🤖 AI confirmation is required.\n⏱️ Signals every 5 minutes.\n"
                        "⚠️ AUTO TRADE: OFF — manual trade only.",
                        reply_markup=mode_markup(uid),
                    )
                    return
                if start_scan(uid):
                    await update.message.reply_text(
                        f"✅ ACCESS OK | MODE: {mode(uid)}\n"
                        "🔎 Fresh AI + technical scan started.\n"
                        "🤖 AI confirmation required.\n⏱️ Automatic cycle: every 5 minutes."
                    )

            final_start._FINAL_START = True
            handler.callback = final_start
            return True
    return False


def patch_access(a):
    application = getattr(a, "telegram_application", None)
    if not application:
        return False
    for handlers in getattr(application, "handlers", {}).values():
        for handler in handlers:
            if "access" not in getattr(handler, "commands", set()):
                continue
            if getattr(handler.callback, "_FINAL_ACCESS", False):
                return True

            async def final_access(update, context):
                a.remember_chat(update)
                args = context.args or []
                expected = os.getenv("ACCESS_CODE")
                if not expected:
                    await update.message.reply_text("ACCESS_CODE is not configured.")
                    return
                code = args[0].strip() if args else ""
                if code != expected:
                    await update.message.reply_text("❌ Invalid access code.")
                    return
                user = getattr(update, "effective_user", None)
                uid = getattr(user, "id", None)
                if uid is None:
                    await update.message.reply_text("❌ Unable to identify this user.")
                    return
                a.authorized_users.add(int(uid))
                await update.message.reply_text(
                    "✅ ACCESS VERIFIED\n\nChoose DEMO or REAL before the scan.",
                    reply_markup=mode_markup(uid),
                )

            final_access._FINAL_ACCESS = True
            handler.callback = final_access
            return True
    return False


def install_routes(a):
    flask = getattr(a, "app", None)
    if flask is None:
        return
    rules = {r.rule for r in flask.url_map.iter_rules()}
    if "/trade/mode" not in rules:
        def trade_mode():
            raw = unpack(request.args.get("token", ""))
            if not raw:
                return "Invalid mode link", 403
            parts = raw.split("|")
            if len(parts) != 3 or parts[1] not in ("DEMO", "REAL"):
                return "Invalid mode link", 400
            try:
                cid, bucket = int(parts[0]), int(parts[2])
            except Exception:
                return "Invalid mode link", 400
            if abs(bucket - int(time.time()) // 600) > 1:
                return "Mode link expired. Send /start again.", 410
            if cid not in authorized_ids(a):
                return "Access not authorized.", 403
            value = parts[1]
            set_mode(cid, value)
            appx = getattr(a, "telegram_application", None)
            if appx is not None and getattr(a, "runtime_loop", None):
                async def notify():
                    await appx.bot.send_message(
                        chat_id=cid,
                        text=f"✅ MODE SELECTED: {value}\n🔎 Fresh AI + technical scan started.\n🤖 AI confirmation required.\n⏱️ Signals every 5 minutes.\n⚠️ AUTO TRADE: OFF — manual trade only."
                    )
                try:
                    asyncio.run_coroutine_threadsafe(notify(), a.runtime_loop)
                except Exception:
                    pass
            start_scan(cid)
            return f"<meta name='viewport' content='width=device-width,initial-scale=1'><body style='font-family:system-ui;padding:28px'><h2>{'🧪' if value == 'DEMO' else '🔴'} {value} MODE SELECTED</h2><p>Fresh AI + technical scan started.</p><b>AUTO TRADE: OFF</b></body>"
        flask.add_url_rule("/trade/mode", endpoint="final_trade_mode", view_func=trade_mode)

    if "/trade/app" not in {r.rule for r in flask.url_map.iter_rules()}:
        def trade_app():
            raw = unpack(request.args.get("token", ""))
            if not raw:
                return "Invalid trade link", 403
            parts = raw.split("|")
            if len(parts) != 6:
                return "Invalid trade link", 400
            cid, pair, direction, entry, expiry_s, bucket_s = parts
            try:
                cid, expiry, bucket = int(cid), int(expiry_s), int(bucket_s)
            except Exception:
                return "Invalid trade link", 400
            if cid not in authorized_ids(a):
                return "Access not authorized.", 403
            if direction not in ("UP", "DOWN") or expiry not in EXPIRIES:
                return "Invalid trade signal", 400
            if abs(bucket - int(time.time()) // 300) > 1:
                return "Trade signal expired. Wait for the next signal.", 410
            selected_mode = mode(cid)
            if selected_mode not in ("DEMO", "REAL"):
                return "Select DEMO or REAL first.", 403
            olymp_url = DEMO_URL if selected_mode == "DEMO" else REAL_URL
            label = "⬆️ UP / BUY" if direction == "UP" else "⬇️ DOWN / SELL"
            return f"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Priyanithan Trade Now</title><style>body{{margin:0;background:#0b1220;color:#fff;font-family:system-ui;padding:18px}}.card{{max-width:520px;margin:auto;background:#172033;border-radius:22px;padding:22px}}.row{{display:flex;justify-content:space-between;padding:14px 0;border-bottom:1px solid #ffffff18}}.label{{color:#9ca3af}}.value{{font-weight:800}}.auto{{color:#86efac;font-size:12px}}.open{{display:block;text-align:center;text-decoration:none;background:#16a34a;color:#fff;padding:17px;border-radius:14px;font-weight:900;margin-top:22px}}</style></head><body><div class="card"><h2>⚡ TRADE NOW</h2><div class="row"><span class="label">MODE</span><span class="value">{html.escape(selected_mode)} <span class="auto">AUTO</span></span></div><div class="row"><span class="label">ASSET</span><span class="value">{html.escape(pair)} <span class="auto">AUTO</span></span></div><div class="row"><span class="label">DIRECTION</span><span class="value">{label} <span class="auto">AUTO</span></span></div><div class="row"><span class="label">ENTRY / PRICE REF</span><span class="value">{html.escape(entry)} <span class="auto">AUTO</span></span></div><div class="row"><span class="label">EXPIRY / TIME</span><span class="value">{expiry} MIN <span class="auto">AUTO</span></span></div><a class="open" href="{html.escape(olymp_url, quote=True)}">OPEN OLYMP TRADE</a><p>Asset, price reference, direction and expiry are carried from the AI-confirmed signal.</p><b>⚠️ AUTO TRADE: OFF — final broker action is manual.</b></div></body></html>"""
        flask.add_url_rule("/trade/app", endpoint="final_trade_app", view_func=trade_app)


def patch_senders(a):
    current = getattr(a, "send_to_recipients", None)
    if current and not getattr(current, "_FINAL_RECIPIENTS", False):
        async def final_recipients(bot, text):
            data = parse_signal(text)
            if not data:
                return await current(bot, text)
            sent = False
            for cid in a.recipients():
                if mode(cid) not in ("DEMO", "REAL"):
                    continue
                try:
                    await bot.send_message(chat_id=cid, text=text, reply_markup=signal_markup(cid, text))
                    sent = True
                except Exception as exc:
                    a.log.warning("FINAL signal send failed %s: %s", cid, exc)
            return sent
        final_recipients._FINAL_RECIPIENTS = True
        a.send_to_recipients = final_recipients


def install():
    a = app()
    if not a:
        return False
    patch_ai(a)
    install_routes(a)
    patch_senders(a)
    patch_start(a)
    patch_access(a)
    return True


try:
    install()
except Exception:
    pass


def bootstrap():
    for _ in range(1800):
        try:
            install()
        except Exception:
            a = app()
            if a and hasattr(a, "log"):
                a.log.exception("FINAL FLOW bootstrap failed")
        time.sleep(1)


threading.Thread(target=bootstrap, name="priyanithan-final-flow", daemon=True).start()

"""External/TradingView-style signal bridge.

Accepts a text/JSON signal, verifies it against fresh OlympTrade candles and
AI, then sends the normal Priyanithan Telegram signal with Trade Now.
Never places a broker order.
"""
import asyncio
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import request

EXPIRIES = (1, 2, 3, 5, 10, 15)
UAE = ZoneInfo("Asia/Dubai")

# Accept the common human-readable signal style shown by the user as well as
# JSON payloads from webhook providers.
_DIRECTION_RE = re.compile(r"(?:TRADE|DIRECTION|SIGNAL)\s*[:=-]?\s*(UP|DOWN)|[⬆️⬇️]\s*(UP|DOWN)", re.I)
_DURATION_RE = re.compile(r"(?:EXPIRY|DURATION|TIMEFRAME)\s*[:=-]?\s*(1|2|3|5|10|15)\s*(?:MIN(?:UTE)?S?|M)?\b|\b(1|2|3|5|10|15)\s*M(?:IN)?\b", re.I)
_ASSET_RE = re.compile(r"(?:📈\s*)?(?:ASSET|PAIR|SYMBOL)?\s*[:=-]?\s*([A-Za-z][A-Za-z0-9_ ./-]{2,60})", re.I)


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _text_payload():
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        for key in ("message", "text", "signal", "content", "body"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value, payload
        # Structured webhook fields.
        return json.dumps(payload, ensure_ascii=False), payload
    raw = request.get_data(as_text=True)
    return raw, {}


def _parse(text, payload=None):
    payload = payload or {}
    # Structured fields take priority.
    asset = payload.get("asset") or payload.get("pair") or payload.get("symbol")
    direction = payload.get("direction") or payload.get("signal")
    duration = payload.get("expiry") or payload.get("duration") or payload.get("timeframe")
    if isinstance(direction, str):
        direction = "UP" if re.search(r"\bUP\b|BUY|CALL|⬆️", direction, re.I) else ("DOWN" if re.search(r"\bDOWN\b|SELL|PUT|⬇️", direction, re.I) else None)
    try:
        duration = int(str(duration).split()[0]) if duration is not None else None
    except Exception:
        duration = None
    s = str(text or "").strip()
    if not asset:
        # Prefer the first line containing a recognizable asset phrase.
        lines = [x.strip() for x in s.splitlines() if x.strip()]
        for line in lines[:6]:
            cleaned = re.sub(r"^[📈📊🔥⚠️\s]+", "", line)
            cleaned = re.sub(r"^(ASSET|PAIR|SYMBOL)\s*[:=-]\s*", "", cleaned, flags=re.I)
            if 3 <= len(cleaned) <= 60 and not re.search(r"TRADE|SIGNAL|INDEX|TIME|MIN|MACD|RSI|STOCH|PARABOLIC|DEMARKER", cleaned, re.I):
                asset = cleaned
                break
        # Explicit example: Asia Composite Index.
        if not asset:
            m = re.search(r"(Asia\s+Composite\s+Index)", s, re.I)
            if m:
                asset = m.group(1)
    if not direction:
        m = _DIRECTION_RE.search(s)
        if m:
            direction = (m.group(1) or m.group(2)).upper()
    if duration not in EXPIRIES:
        m = _DURATION_RE.search(s)
        if m:
            duration = int(m.group(1) or m.group(2))
    if not asset or direction not in ("UP", "DOWN") or duration not in EXPIRIES:
        return None
    return {"asset": str(asset).strip(), "direction": direction, "expiry": duration, "raw": s}


def _resolve_pair(a, asset):
    name = str(asset).strip().upper()
    aliases = getattr(a, "PAIR_ALIASES", {})
    for key, value in aliases.items():
        if key in name or name == str(key).upper():
            name = str(value).upper()
            break
    discovered = getattr(a, "discovered_assets", {}) or {}
    if name in discovered:
        return name
    # Exact/substring match against live broker names.
    compact = re.sub(r"[^A-Z0-9]", "", name)
    for candidate in discovered:
        cc = re.sub(r"[^A-Z0-9]", "", str(candidate).upper())
        if compact == cc or compact in cc or cc in compact:
            return candidate
    # Known Asia Composite naming used by the project.
    if "ASIA" in name and ("COMPOSITE" in name or "INDEX" in name):
        target = str(aliases.get("ASIA_X", "ASIA_X")).upper()
        if target in discovered:
            return target
        for candidate in discovered:
            if "ASIA" in str(candidate).upper():
                return candidate
    # Fall back only to a configured manual pair; never invent a broker symbol.
    manual = getattr(a, "MANUAL_PAIRS", []) or []
    for candidate in manual:
        if name == str(candidate).upper():
            return candidate
    return None


def _indicator_text(raw):
    names = ("Parabolic SAR", "DeMarker", "Stochastic", "RSI", "MACD")
    found = [n for n in names if re.search(re.escape(n), raw, re.I)]
    return found


async def _process(parsed, source="external"):
    a = _app()
    if a is None:
        return
    pair = _resolve_pair(a, parsed["asset"])
    if not pair:
        a.log.warning("EXTERNAL SIGNAL REJECTED: asset not found in live broker catalogue: %s", parsed["asset"])
        return
    getter = getattr(a, "get_ot_candles", None)
    analyzer = getattr(a, "analyze_pair", None)
    ai_call = getattr(a, "call_ai", None)
    if not getter or not analyzer or not ai_call:
        a.log.warning("EXTERNAL SIGNAL REJECTED: runtime analysis functions unavailable")
        return
    df, err = await getter(pair, 60, 120)
    if df is None:
        a.log.warning("EXTERNAL SIGNAL REJECTED: live candles unavailable pair=%s reason=%s", pair, err)
        return
    try:
        technical = analyzer(pair, df)
    except Exception as exc:
        a.log.warning("EXTERNAL SIGNAL TECHNICAL CHECK FAILED pair=%s: %s", pair, exc)
        return
    external_direction = parsed["direction"]
    if str(technical.get("signal", "NO SIGNAL")).upper() != external_direction:
        a.log.info("EXTERNAL SIGNAL REJECTED: direction mismatch external=%s technical=%s pair=%s", external_direction, technical.get("signal"), pair)
        return
    # Use the current live candle close as the manual entry/reference price.
    try:
        entry = float(df["close"].iloc[-1])
    except Exception:
        a.log.warning("EXTERNAL SIGNAL REJECTED: live entry unavailable pair=%s", pair)
        return
    enriched = dict(technical)
    enriched.update({
        "external_asset": parsed["asset"],
        "external_direction": external_direction,
        "external_expiry_min": parsed["expiry"],
        "external_indicators": _indicator_text(parsed["raw"]),
        "external_source": source,
        "external_time_uae": datetime.now(UAE).strftime("%H:%M:%S UAE"),
        "price": entry,
    })
    prompt_fn = getattr(a, "ai_prompt", lambda x: json.dumps(x, ensure_ascii=False))
    try:
        ai, ai_err = await asyncio.wait_for(asyncio.to_thread(ai_call, prompt_fn(enriched)), timeout=30)
    except Exception as exc:
        a.log.warning("EXTERNAL SIGNAL AI CHECK FAILED pair=%s: %s", pair, exc)
        return
    if not ai or ai_err:
        a.log.warning("EXTERNAL SIGNAL AI REJECTED/FAILED pair=%s reason=%s", pair, ai_err)
        return
    decision = str(ai.get("decision", "")).upper()
    ai_direction = str(ai.get("direction", "")).upper()
    try:
        confidence = int(ai.get("confidence", 0))
    except Exception:
        confidence = 0
    if decision != "APPROVE" or ai_direction != external_direction or confidence < int(getattr(a, "AI_MIN_CONFIDENCE", 60)):
        a.log.info("EXTERNAL SIGNAL REJECTED BY AI pair=%s decision=%s direction=%s confidence=%s", pair, decision, ai_direction, confidence)
        return
    # External source defines the requested expiry; AI confirms the setup but
    # does not silently change the user's 1/2/3/5/10/15 minute duration.
    ai = dict(ai)
    ai["direction"] = external_direction
    ai["duration_min"] = parsed["expiry"]
    formatter = getattr(sys.modules.get("signal_engine"), "_classic_signal_text", None)
    if formatter is None:
        # Recreate the canonical compact format if the module is not loaded.
        arrow = "⬆️" if external_direction == "UP" else "⬇️"
        text = (f"🔥 PRIYANITHAN AI SIGNAL 🔥\n\n📈 {pair}\n\n{arrow} {external_direction}\n\n"
                f"💰 Entry: {entry}\n\n⏱️ Expiry: {parsed['expiry']} MIN\n\n🤖 AI Confidence: {confidence}%\n\n"
                f"📊 Technical: {int(technical.get('confidence', 0))}%\n\n🕐 {datetime.now(UAE).strftime('%H:%M:%S UAE')}\n\n"
                "🧠 Candice AI: APPROVED\n\n⚠️ MANUAL TRADE — AUTO TRADE OFF")
    else:
        text = formatter(enriched, ai)
    sender = getattr(a, "send_to_recipients", None)
    appx = getattr(a, "telegram_application", None)
    if sender and appx:
        sent = await sender(appx.bot, text)
        a.log.info("EXTERNAL QUALIFIED SIGNAL SENT: pair=%s direction=%s expiry=%s confidence=%s sent=%s", pair, external_direction, parsed["expiry"], confidence, sent)


def _route():
    a = _app()
    if a is None or getattr(a, "app", None) is None:
        return False
    flask = a.app
    if "/webhook/tradingview" not in {r.rule for r in flask.url_map.iter_rules()}:
        @flask.post("/webhook/tradingview")
        def tradingview_webhook():
            expected = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "").strip()
            supplied = request.headers.get("X-Webhook-Secret", "").strip() or request.args.get("secret", "").strip()
            if expected and supplied != expected:
                return "Unauthorized", 401
            text, payload = _text_payload()
            parsed = _parse(text, payload)
            if not parsed:
                return "Invalid signal payload", 400
            loop = getattr(a, "runtime_loop", None)
            if loop is None:
                return "Bot runtime not ready", 503
            asyncio.run_coroutine_threadsafe(_process(parsed, "TradingView/webhook"), loop)
            return {"ok": True, "accepted": True, "asset": parsed["asset"], "direction": parsed["direction"], "expiry": parsed["expiry"]}, 202
    return True


def _boot():
    for _ in range(1800):
        try:
            if _route():
                a = _app()
                if a and hasattr(a, "log"):
                    a.log.warning("EXTERNAL SIGNAL BRIDGE ACTIVE: POST /webhook/tradingview")
                return
        except Exception:
            a = _app()
            if a and hasattr(a, "log"):
                a.log.exception("EXTERNAL SIGNAL BRIDGE INSTALL FAILED")
        time.sleep(1)

threading.Thread(target=_boot, name="external-signal-bridge", daemon=True).start()

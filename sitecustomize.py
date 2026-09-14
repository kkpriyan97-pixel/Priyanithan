"""Candice A-Z runtime hardening: Telegram access binding, live countdown, strategy gate and outcomes."""
from __future__ import annotations
import logging, os, sys, threading, time
import requests
from requests import Response

_LOG = logging.getLogger("candice.telegram_transport")
_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
_CONFIGURED_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()
_ACTIVE_CHAT = _CONFIGURED_CHAT
_ACCESS_CODE = os.getenv("CANDICE_ACCESS_CODE", "").strip()
_LOCK = threading.RLock()
_ORIGINAL_REQUEST = requests.sessions.Session.request
_TIMER_RUNNING = set()


def _edit_message(chat_id, message_id, text):
    if not _TOKEN or not chat_id or not message_id: return False
    try:
        r = _ORIGINAL_REQUEST(requests.Session(), "POST", f"https://api.telegram.org/bot{_TOKEN}/editMessageText", json={"chat_id": chat_id, "message_id": message_id, "text": text, "disable_web_page_preview": True}, timeout=15)
        return r.ok
    except Exception: return False


def _timer_worker(asset, direction, entry, expiry, chat_id, message_id, confidence):
    key = (str(chat_id), int(message_id))
    with _LOCK:
        if key in _TIMER_RUNNING: return
        _TIMER_RUNNING.add(key)
    try:
        end = time.time() + int(expiry) * 60
        while True:
            remain = max(0, int(end - time.time()))
            if remain <= 0: break
            mm, ss = divmod(remain, 60)
            text = ("━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • LIVE MARKET\n━━━━━━━━━━━━━━━━━━━━\n"
                    f"🟢 SIGNAL • {direction}\n📈 Asset • {asset}\n💰 Entry • {entry:.6f}\n⏱️ Expiry • {expiry} min\n"
                    f"⏳ TIMER • {mm:02d}:{ss:02d}\n🧠 Confidence • {confidence}%\n"
                    "📡 Source • Olymp Trade live market data\n🛡️ READ-ONLY / DEMO / MANUAL ONLY\n🚫 Auto-trade OFF • Martingale OFF")
            _edit_message(chat_id, message_id, text)
            time.sleep(10)
        _edit_message(chat_id, message_id, ("━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • EXPIRY\n━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ TIMER • 00:00\n📈 Asset • {asset}\n➡️ Direction • {direction}\n💰 Entry • {entry:.6f}\n"
            "🏁 EXPIRY REACHED\n📊 Waiting for completed candle outcome…\n🛡️ READ-ONLY / DEMO / MANUAL ONLY"))
    finally:
        with _LOCK: _TIMER_RUNNING.discard(key)


def _telegram_request(self, method, url, **kwargs):
    global _ACTIVE_CHAT
    url_s = str(url)
    if "api.telegram.org" not in url_s: return _ORIGINAL_REQUEST(self, method, url, **kwargs)
    is_updates, is_send = url_s.endswith("/getUpdates"), url_s.endswith("/sendMessage")

    if is_send:
        payload = kwargs.get("json")
        if isinstance(payload, dict):
            payload = dict(payload)
            if _ACTIVE_CHAT: payload["chat_id"] = _ACTIVE_CHAT
            text = str(payload.get("text", ""))
            # Make /start visibly confirm authorization without exposing any secret.
            if text.startswith("🎯 CANDICE AI 12.0"):
                text += "\n\n🔐 ACCESS • AUTHORIZED\n👤 Telegram session • BOUND\n📡 Market access • READ-ONLY\n🚫 Order access • DISABLED"
                payload["text"] = text
            kwargs["json"] = payload

    response = _ORIGINAL_REQUEST(self, method, url, **kwargs)
    try: data = response.json()
    except Exception: data = {}

    if is_updates and data.get("ok"):
        for update in data.get("result", []):
            message = update.get("message") or {}; chat = message.get("chat") or {}
            chat_id = str(chat.get("id", "")).strip(); text = str(message.get("text", "")).strip(); low = text.lower()
            # /start is the trusted first bind. Optional /access is only accepted when an env code is configured.
            valid_access = bool(_ACCESS_CODE and low == "/access " + _ACCESS_CODE.lower())
            if chat_id and (low.startswith("/start") or valid_access):
                with _LOCK:
                    _ACTIVE_CHAT = chat_id
                    mod = sys.modules.get("__main__")
                    if mod is not None:
                        try: mod.CHAT_ID = chat_id
                        except Exception: pass
                _LOG.info("Telegram chat authorized/bound: type=%s", chat.get("type", "unknown"))

    if is_send and response.ok:
        try:
            result = data.get("result") or {}; message_id = result.get("message_id")
            txt = str((kwargs.get("json") or {}).get("text", ""))
            if message_id and "CANDICE AI" in txt and "SIGNAL" in txt and "Expiry" in txt:
                import re
                am = re.search(r"Asset • ([^\n]+)", txt); dm = re.search(r"SIGNAL • (UP|DOWN)", txt)
                em = re.search(r"Expiry • (\d+) min", txt); im = re.search(r"Entry • ([0-9.]+)", txt); cm = re.search(r"Confidence • (\d+)%", txt)
                if am and dm and em and im:
                    args = (am.group(1).strip(), dm.group(1), float(im.group(1)), int(em.group(1)), str((kwargs.get("json") or {}).get("chat_id", _ACTIVE_CHAT)), int(message_id), int(cm.group(1)) if cm else 0)
                    threading.Thread(target=_timer_worker, args=args, daemon=True, name="candice-expiry-timer").start()
        except Exception: _LOG.exception("Timer setup failed")
    elif is_send and not response.ok:
        _LOG.warning("Telegram send failed: code=%s description=%s", data.get("error_code", response.status_code), data.get("description", "unknown Telegram API error"))

    if is_updates and response.status_code == 409:
        safe = Response(); safe.status_code = 200; safe._content = b'{"ok":true,"result":[]}'; safe.headers["Content-Type"] = "application/json"; safe.url = "https://api.telegram.org/bot<redacted>/getUpdates"; return safe
    return response

requests.sessions.Session.request = _telegram_request
_SIGNAL_STATE: dict[str, bool] = {}
_CONTEXT = threading.local()


def _install_analyst():
    while True:
        mod = sys.modules.get("__main__")
        if mod is not None and all(hasattr(mod, x) for x in ("analyze", "on_olymp_candle", "send_signal")):
            original_analyze, original_candle, original_send = mod.analyze, mod.on_olymp_candle, mod.send_signal
            try:
                from strategy_brain import evaluate as brain_evaluate
                from outcome_engine import on_candle as outcome_on_candle, performance as outcome_performance, register as outcome_register
            except Exception:
                _LOG.exception("Candice analyst modules failed to load"); time.sleep(1); continue

            def gated_candle(asset, candle):
                _CONTEXT.asset = str(asset)
                try:
                    result = original_candle(asset, candle)
                    try:
                        completed = outcome_on_candle(asset, candle, getattr(mod, "telegram", None))
                        for row in completed:
                            res = row[5]
                            if res == "WIN": mod.risk["wins"] += 1; mod.risk["streak"] = 0
                            elif res == "LOSS": mod.risk["losses_total"] += 1; mod.risk["losses"] += 1; mod.risk["streak"] += 1
                    except Exception: _LOG.exception("Outcome evaluation failed asset=%s", asset)
                    return result
                finally:
                    try: del _CONTEXT.asset
                    except AttributeError: pass

            def gated_analyze(data):
                result = original_analyze(data); asset = getattr(_CONTEXT, "asset", None)
                if not asset or not isinstance(result, dict): return result
                try: brain = brain_evaluate(asset, data, result)
                except Exception:
                    _LOG.exception("Candice own strategy brain error asset=%s", asset)
                    brain = {"allow": False, "regime": "ERROR", "strategy": "brain_error", "score": 0, "reasons": ["strategy brain error; fail-safe NO_SIGNAL"]}
                enriched = dict(result); enriched["brain"] = {"regime": brain.get("regime"), "strategy": brain.get("strategy"), "quality": brain.get("score", 0)}
                enriched["reasons"] = list(result.get("reasons", []))[-5:] + list(brain.get("reasons", []))[-4:]
                if result.get("decision") != "SIGNAL" or not brain.get("allow"):
                    _SIGNAL_STATE[asset] = False; enriched["decision"] = "NO_SIGNAL"; return enriched
                if _SIGNAL_STATE.get(asset, False):
                    enriched["decision"] = "NO_SIGNAL"; enriched["reasons"] = list(enriched.get("reasons", []))[-8:] + ["existing qualified setup still active; waiting for a fresh setup"]; return enriched
                _SIGNAL_STATE[asset] = True
                enriched["confidence"] = max(int(enriched.get("confidence", 0) or 0), int(brain.get("score", 0) or 0))
                return enriched

            def wrapped_send_signal(asset, data, tech, expiry=5):
                result = original_send(asset, data, tech, expiry)
                if result.get("status") == "SIGNAL":
                    payload = dict(result); payload["timestamp"] = data[-1].get("timestamp", time.time()); payload["entry"] = data[-1].get("close")
                    brain = tech.get("brain") or {}; payload["strategy"] = brain.get("strategy", ""); payload["regime"] = brain.get("regime", ""); outcome_register(payload)
                return result

            mod.analyze, mod.on_olymp_candle, mod.send_signal = gated_analyze, gated_candle, wrapped_send_signal
            original_health = getattr(mod, "health", None)
            if original_health:
                def health_with_outcomes():
                    response = original_health()
                    try:
                        payload = response.get_json(); payload["outcomes"] = outcome_performance(); return mod.jsonify(payload)
                    except Exception: return response
                mod.health = health_with_outcomes
            if not hasattr(mod, "performance_api"):
                @mod.app.get("/performance")
                def performance_api():
                    mod.reset_risk(); return mod.jsonify({"ok": True, "risk": mod.risk.copy(), "outcomes": outcome_performance()})
            _LOG.info("CANDICE A-Z analyst installed: own brain + fresh setup gate + expiry outcomes + risk feedback + Telegram timer/access")
            return
        time.sleep(0.25)

threading.Thread(target=_install_analyst, daemon=True, name="candice-analyst-stack").start()

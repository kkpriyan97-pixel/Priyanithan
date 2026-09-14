"""Candice runtime hardening: single Telegram poller, access gate, one active signal, timer, outcomes."""
from __future__ import annotations
import json, logging, os, re, sys, threading, time
import requests
from requests import Response

LOG = logging.getLogger("candice.runtime")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CONFIGURED_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ACCESS_CODE = os.getenv("CANDICE_ACCESS_CODE", "").strip()
ACTIVE_CHAT = CONFIGURED_CHAT
AUTHORIZED = False
LOCK = threading.RLock()
ORIGINAL_REQUEST = requests.sessions.Session.request
TIMERS = set()
ACTIVE_SIGNALS: dict[str, float] = {}
CONTEXT = threading.local()
POLL_STARTED = False


def _raw(method, path, **kwargs):
    if not TOKEN:
        return None
    try:
        return ORIGINAL_REQUEST(requests.Session(), method, f"https://api.telegram.org/bot{TOKEN}/{path}", **kwargs)
    except Exception:
        LOG.exception("Telegram direct request failed")
        return None


def _raw_post(path, payload):
    return _raw("POST", path, json=payload, timeout=15)


def _send_access(chat_id, text):
    if chat_id:
        _raw_post("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})


def _edit(chat_id, message_id, text):
    if not TOKEN or not chat_id or not message_id:
        return False
    try:
        r = _raw_post("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text, "disable_web_page_preview": True})
        return bool(r and r.ok)
    except Exception:
        return False


def _status_text():
    mod = sys.modules.get("__main__")
    try:
        s = mod.live_feed.status() if mod and getattr(mod, "live_feed", None) else {"connected": False, "assets": []}
        assets = ", ".join(s.get("assets", [])) or "-"
        return ("🎯 STATUS\n\n"
                f"📡 Olymp • {'🟢 CONNECTED' if s.get('connected') else '🔴 NOT CONNECTED'}\n"
                f"📈 Assets • {assets}\n"
                "🕐 1m live + 3m/5m derived\n🧠 Adaptive brain • ON\n"
                "🛡️ Read-only • YES\n🚫 Auto-trade • OFF")
    except Exception:
        return "🎯 STATUS\n\n⚠️ Candice is starting; live status not ready yet."


def _performance_text():
    mod = sys.modules.get("__main__")
    try:
        mod.reset_risk()
        r = mod.risk
        return ("📊 SESSION\n"
                f"Signals • {r['signals']}\nWins • {r['wins']}\nLosses • {r['losses_total']}\n"
                f"Streak • {r['streak']}\nDaily stop • {mod.MAX_DAILY_LOSSES}\n"
                f"Consecutive stop • {mod.MAX_CONSECUTIVE_LOSSES}")
    except Exception:
        return "📊 SESSION\n⚠️ Performance is not ready yet."


def _handle_update(update):
    global ACTIVE_CHAT, AUTHORIZED
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    cid = str(chat.get("id", "")).strip()
    text = str(msg.get("text", "")).strip()
    if not cid or not text:
        return
    low = text.lower()
    if low.startswith("/start"):
        with LOCK:
            ACTIVE_CHAT = cid
            AUTHORIZED = False
        _send_access(cid, "🔐 CANDICE AI ACCESS\n\nTelegram session detected.\n\nSend:\n/access YOUR_ACCESS_CODE\n\n⚠️ /start alone does NOT authorize access.")
        return
    if low.startswith("/access "):
        supplied = text.split(" ", 1)[1].strip()
        ok = bool(ACCESS_CODE) and supplied.casefold() == ACCESS_CODE.casefold()
        with LOCK:
            if ok:
                ACTIVE_CHAT = cid
                AUTHORIZED = True
        _send_access(cid, ("🎯 CANDICE AI 12.0\n\n✅ ONLINE\n🔐 ACCESS • AUTHORIZED\n👤 Telegram session • BOUND\n"
                           "📡 Market access • READ-ONLY\n🚫 Order access • DISABLED\n\n/status • connection\n/performance • session"
                           if ok else "❌ ACCESS • DENIED\n\nInvalid access code. No market signals will be delivered."))
        return
    with LOCK:
        active = cid == ACTIVE_CHAT
        authorized = AUTHORIZED
    if not active or not authorized:
        return
    if low.startswith("/status"):
        _send_access(cid, _status_text())
    elif low.startswith("/performance"):
        _send_access(cid, _performance_text())


def _telegram_poller():
    """The only Telegram getUpdates owner. This prevents competing pollers and 409 loops."""
    global POLL_STARTED
    if not TOKEN:
        LOG.warning("Telegram poller disabled: token not configured")
        return
    with LOCK:
        if POLL_STARTED:
            return
        POLL_STARTED = True
    offset = 0
    LOG.info("Telegram single-owner poller started")
    while True:
        try:
            r = _raw("GET", "getUpdates", params={"timeout": 25, "offset": offset + 1, "allowed_updates": json.dumps(["message"])}, timeout=35)
            if r is None:
                time.sleep(5); continue
            if r.status_code == 409:
                LOG.warning("Telegram polling conflict (409); another poller is active; backing off 15s")
                time.sleep(15)
                continue
            if r.status_code == 401:
                LOG.error("Telegram token rejected (401); token value is never logged")
                time.sleep(30)
                continue
            r.raise_for_status()
            data = r.json()
            for update in data.get("result", []):
                offset = max(offset, int(update.get("update_id", offset)))
                try:
                    _handle_update(update)
                except Exception:
                    LOG.exception("Telegram update handling failed")
        except Exception as e:
            LOG.warning("Telegram poller error: %s", type(e).__name__)
            time.sleep(5)


def _timer(chat_id, message_id, asset, direction, entry, expiry, confidence):
    key = (str(chat_id), int(message_id))
    with LOCK:
        if key in TIMERS:
            return
        TIMERS.add(key)
    try:
        deadline = time.time() + int(expiry) * 60
        while True:
            remain = max(0, int(deadline - time.time()))
            if remain <= 0:
                break
            m, s = divmod(remain, 60)
            _edit(chat_id, message_id, "━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • LIVE MARKET\n━━━━━━━━━━━━━━━━━━━━\n"
                  f"🟢 SIGNAL • {direction}\n📈 Asset • {asset}\n💰 Entry • {entry:.6f}\n⏱️ Expiry • {expiry} min\n"
                  f"⏳ TIMER • {m:02d}:{s:02d}\n🧠 Confidence • {confidence}%\n"
                  "📡 Source • Olymp Trade live market data\n🛡️ READ-ONLY / DEMO / MANUAL ONLY\n🚫 Auto-trade OFF • Martingale OFF")
            time.sleep(5)
        _edit(chat_id, message_id, "━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • EXPIRY\n━━━━━━━━━━━━━━━━━━━━\n"
              "⏳ TIMER • 00:00\n" f"📈 Asset • {asset}\n➡️ Direction • {direction}\n💰 Entry • {entry:.6f}\n"
              "🏁 EXPIRY REACHED\n📊 Waiting for completed candle outcome…\n🛡️ READ-ONLY / DEMO / MANUAL ONLY")
    finally:
        with LOCK:
            TIMERS.discard(key)
            ACTIVE_SIGNALS.pop(asset, None)


def _telegram_request(self, method, url, **kwargs):
    global ACTIVE_CHAT, AUTHORIZED
    u = str(url)
    if "api.telegram.org" not in u:
        return ORIGINAL_REQUEST(self, method, url, **kwargs)
    is_send = u.endswith("/sendMessage")
    if is_send:
        payload = kwargs.get("json")
        if isinstance(payload, dict):
            payload = dict(payload)
            text = str(payload.get("text", ""))
            signal = "CANDICE AI" in text and "SIGNAL" in text and "Expiry" in text
            if signal:
                with LOCK:
                    allowed = AUTHORIZED and bool(ACTIVE_CHAT)
                    target = ACTIVE_CHAT
                if not allowed:
                    blocked = Response(); blocked.status_code = 200
                    blocked._content = b'{"ok":true,"result":{"message_id":0}}'
                    blocked.headers["Content-Type"] = "application/json"
                    return blocked
                payload["chat_id"] = target
            elif ACTIVE_CHAT:
                payload["chat_id"] = ACTIVE_CHAT
            kwargs["json"] = payload
    response = ORIGINAL_REQUEST(self, method, url, **kwargs)
    if is_send and response.ok:
        try:
            data = response.json()
            with LOCK:
                allowed = AUTHORIZED
            txt = str((kwargs.get("json") or {}).get("text", ""))
            mid = (data.get("result") or {}).get("message_id")
            if allowed and mid and "CANDICE AI" in txt and "SIGNAL" in txt and "Expiry" in txt:
                am = re.search(r"Asset • ([^\n]+)", txt); dm = re.search(r"SIGNAL • (UP|DOWN)", txt)
                em = re.search(r"Expiry • (\d+) min", txt); im = re.search(r"Entry • ([0-9.]+)", txt); cm = re.search(r"Confidence • (\d+)%", txt)
                if am and dm and em and im:
                    asset, expiry = am.group(1).strip(), int(em.group(1))
                    with LOCK: ACTIVE_SIGNALS[asset] = time.time() + expiry * 60
                    args = (ACTIVE_CHAT, int(mid), asset, dm.group(1), float(im.group(1)), expiry, int(cm.group(1)) if cm else 0)
                    threading.Thread(target=_timer, args=args, daemon=True, name="candice-expiry-timer").start()
        except Exception:
            LOG.exception("Timer setup failed")
    return response


requests.sessions.Session.request = _telegram_request


def _install_analyst():
    while True:
        mod = sys.modules.get("__main__")
        if mod is not None and all(hasattr(mod, x) for x in ("analyze", "on_olymp_candle", "send_signal")):
            original_analyze, original_candle, original_send = mod.analyze, mod.on_olymp_candle, mod.send_signal
            try:
                from strategy_brain import evaluate as brain_evaluate
                from outcome_engine import on_candle as outcome_on_candle, performance as outcome_performance, register as outcome_register
            except Exception:
                LOG.exception("Analyst modules failed to load"); time.sleep(1); continue

            def gated_candle(asset, candle):
                CONTEXT.asset = str(asset)
                try:
                    result = original_candle(asset, candle)
                    try:
                        completed = outcome_on_candle(asset, candle, getattr(mod, "telegram", None))
                        for row in completed:
                            if row[5] == "WIN": mod.risk["wins"] += 1; mod.risk["streak"] = 0
                            elif row[5] == "LOSS": mod.risk["losses_total"] += 1; mod.risk["losses"] += 1; mod.risk["streak"] += 1
                    except Exception: LOG.exception("Outcome evaluation failed asset=%s", asset)
                    return result
                finally:
                    try: del CONTEXT.asset
                    except AttributeError: pass

            def gated_analyze(data):
                result = original_analyze(data); asset = getattr(CONTEXT, "asset", None)
                if not asset or not isinstance(result, dict): return result
                try: brain = brain_evaluate(asset, data, result)
                except Exception:
                    LOG.exception("Strategy brain error asset=%s", asset)
                    brain = {"allow": False, "regime": "ERROR", "strategy": "brain_error", "score": 0, "reasons": ["strategy brain error; fail-safe NO_SIGNAL"]}
                enriched = dict(result)
                enriched["brain"] = {"regime": brain.get("regime"), "strategy": brain.get("strategy"), "quality": brain.get("score", 0)}
                enriched["reasons"] = list(result.get("reasons", []))[-5:] + list(brain.get("reasons", []))[-4:]
                if result.get("decision") != "SIGNAL" or not brain.get("allow"):
                    enriched["decision"] = "NO_SIGNAL"; return enriched
                with LOCK:
                    deadline = ACTIVE_SIGNALS.get(asset); active = bool(deadline and deadline > time.time())
                    if deadline and deadline <= time.time(): ACTIVE_SIGNALS.pop(asset, None)
                if active:
                    enriched["decision"] = "NO_SIGNAL"; enriched["reasons"] = list(enriched.get("reasons", []))[-8:] + ["active signal still within expiry; duplicate blocked"]; return enriched
                enriched["confidence"] = max(int(enriched.get("confidence", 0) or 0), int(brain.get("score", 0) or 0))
                return enriched

            def wrapped_send_signal(asset, data, tech, expiry=5):
                with LOCK:
                    if not AUTHORIZED: return {"status": "NO_SIGNAL", "reason": "Telegram access not authorized"}
                    deadline = ACTIVE_SIGNALS.get(str(asset))
                    if deadline and deadline > time.time(): return {"status": "NO_SIGNAL", "reason": "active signal still within expiry"}
                result = original_send(asset, data, tech, expiry)
                if result.get("status") == "SIGNAL":
                    payload = dict(result); payload["timestamp"] = data[-1].get("timestamp", time.time()); payload["entry"] = data[-1].get("close")
                    brain = tech.get("brain") or {}; payload["strategy"] = brain.get("strategy", ""); payload["regime"] = brain.get("regime", "")
                    try: outcome_register(payload)
                    except Exception: LOG.exception("Outcome registration failed")
                return result

            mod.analyze = gated_analyze; mod.on_olymp_candle = gated_candle; mod.send_signal = wrapped_send_signal
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
                    mod.reset_risk(); return mod.jsonify({"ok": True, "risk": mod.risk.copy(), "outcomes": outcome_performance(), "telegram_authorized": AUTHORIZED})
            LOG.info("CANDICE A-Z analyst installed: single Telegram poller + access gate + one-active-signal lock + live timer + outcomes + strategy brain")
            threading.Thread(target=_telegram_poller, daemon=True, name="candice-telegram-owner").start()
            return
        time.sleep(0.25)

threading.Thread(target=_install_analyst, daemon=True, name="candice-analyst-stack").start()

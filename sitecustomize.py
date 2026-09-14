"""Candice runtime overlay: read-only market brain + safe five-minute scheduler."""
from __future__ import annotations
import json, logging, os, re, sys, threading, time
import requests
from requests import Response

LOG = logging.getLogger("candice.runtime")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CONFIGURED_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ACCESS_CODE = os.getenv("CANDICE_ACCESS_CODE", "").strip()
LOCK = threading.RLock()
ACTIVE_CHAT = CONFIGURED_CHAT
AUTHORIZED = bool(CONFIGURED_CHAT)
POLL_STARTED = False
ORIGINAL_REQUEST = requests.sessions.Session.request
ACTIVE_SIGNALS = {}
CANDIDATES = {}
SENT_WINDOWS = set()
SUPPORTED_EXPIRIES = {1, 2, 3, 4, 5, 10, 15}


def _raw(method, path, **kwargs):
    if not TOKEN:
        return None
    try:
        return ORIGINAL_REQUEST(requests.Session(), method, f"https://api.telegram.org/bot{TOKEN}/{path}", **kwargs)
    except Exception:
        return None


def _send(chat, text):
    if chat:
        _raw("POST", "sendMessage", json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=15)


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
            if not CONFIGURED_CHAT or cid == CONFIGURED_CHAT:
                ACTIVE_CHAT, AUTHORIZED = cid, True
        if cid == ACTIVE_CHAT and AUTHORIZED:
            _send(cid, "🎯 CANDICE AI\n\n✅ ONLINE\n🔐 ACCESS • AUTHORIZED\n👤 Telegram session • BOUND\n📡 Market access • READ-ONLY\n🚫 Order access • DISABLED\n\n/status • connection\n/performance • session")
        else:
            _send(cid, "🔐 CANDICE AI ACCESS\n\nSend /access YOUR_ACCESS_CODE")
        return
    if low.startswith("/access "):
        supplied = text.split(" ", 1)[1].strip()
        ok = bool(ACCESS_CODE) and supplied.casefold() == ACCESS_CODE.casefold()
        with LOCK:
            if ok:
                ACTIVE_CHAT, AUTHORIZED = cid, True
        _send(cid, "🎯 CANDICE AI\n\n✅ ONLINE\n🔐 ACCESS • AUTHORIZED\n👤 Telegram session • BOUND\n📡 Market access • READ-ONLY\n🚫 Order access • DISABLED\n\n/status • connection\n/performance • session" if ok else "❌ ACCESS • DENIED")
        return
    with LOCK:
        allowed = AUTHORIZED and cid == ACTIVE_CHAT
    if not allowed:
        return
    mod = sys.modules.get("__main__")
    if low.startswith("/status"):
        try:
            s = mod.live_feed.status()
            assets = s.get("assets") or []
            connected = bool(s.get("connected"))
            _send(cid, f"🎯 CANDICE STATUS\n\n📡 Olymp • {'🟢 CONNECTED' if connected else '🔴 DISCONNECTED'}\n📈 Assets discovered • {len(assets)}\n🕐 Real 1m candles • ON\n🧠 Market/Candle Brain • ON\n📊 Indicators • confirmation only\n🛡️ Read-only • YES\n🚫 Auto-trade • OFF\n🚫 Martingale • OFF")
        except Exception:
            _send(cid, "🎯 CANDICE STATUS\n\n🔴 Market status temporarily unavailable")
    elif low.startswith("/performance"):
        try:
            from outcome_engine import performance
            p = performance()
            _send(cid, f"📊 CANDICE PERFORMANCE\n\nSignals • {p['evaluated']}\nWins • {p['wins']}\nLosses • {p['losses']}\nWin rate • {p['win_rate']}%")
        except Exception:
            _send(cid, "📊 Performance is not ready yet.")


def _poller():
    global POLL_STARTED
    with LOCK:
        if POLL_STARTED:
            return
        POLL_STARTED = True
    if not TOKEN:
        LOG.warning("Telegram poller disabled: token missing")
        return
    offset = 0
    LOG.info("Telegram owner poller started authorized=%s configured_chat=%s", AUTHORIZED, bool(CONFIGURED_CHAT))
    while True:
        try:
            r = _raw("GET", "getUpdates", params={"timeout": 25, "offset": offset + 1, "allowed_updates": json.dumps(["message"])}, timeout=35)
            if r is None:
                time.sleep(5); continue
            if r.status_code in (409, 429):
                time.sleep(10); continue
            if r.status_code == 401:
                time.sleep(30); continue
            r.raise_for_status()
            for u in r.json().get("result", []):
                offset = max(offset, int(u.get("update_id", offset)))
                try:
                    _handle_update(u)
                except Exception:
                    LOG.exception("Telegram update handling failed")
        except Exception:
            time.sleep(5)


def _timer(chat, message_id, asset, direction, entry, expiry, confidence):
    deadline = time.time() + expiry * 60
    while time.time() < deadline:
        remaining = max(0, int(deadline - time.time()))
        mm, ss = divmod(remaining, 60)
        _raw("POST", "editMessageText", json={"chat_id": chat, "message_id": message_id, "text": f"━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • LIVE MARKET\n━━━━━━━━━━━━━━━━━━━━\n🟢 SIGNAL • {direction}\n📈 Asset • {asset}\n💰 Entry • {entry:.6f}\n⏱️ Expiry • {expiry} min\n⏳ TIMER • {mm:02d}:{ss:02d}\n🧠 Confidence • {confidence}%\n📡 Source • Olymp Trade live market data\n🛡️ READ-ONLY / DEMO / MANUAL ONLY\n🚫 Auto-trade OFF • Martingale OFF"}, timeout=15)
        time.sleep(5)
    _raw("POST", "editMessageText", json={"chat_id": chat, "message_id": message_id, "text": f"━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • EXPIRY\n━━━━━━━━━━━━━━━━━━━━\n📈 Asset • {asset}\n➡️ Direction • {direction}\n⏱️ Expiry • {expiry} min\n🏁 EXPIRY REACHED\n📊 Waiting for completed candle outcome…\n🛡️ READ-ONLY / DEMO / MANUAL ONLY"}, timeout=15)
    with LOCK:
        ACTIVE_SIGNALS.pop(asset, None)


def _telegram_request(self, method, url, **kwargs):
    if "api.telegram.org" not in str(url):
        return ORIGINAL_REQUEST(self, method, url, **kwargs)
    is_send = str(url).endswith("/sendMessage")
    if is_send:
        payload = kwargs.get("json")
        if isinstance(payload, dict):
            payload = dict(payload)
            text = str(payload.get("text", ""))
            is_signal = "CANDICE AI" in text and "SIGNAL" in text and "Expiry" in text
            with LOCK:
                auth, target = AUTHORIZED, ACTIVE_CHAT
            if is_signal and not auth:
                fake = Response(); fake.status_code = 200; fake._content = b'{"ok":true,"result":{"message_id":0}}'; return fake
            if target:
                payload["chat_id"] = target
            kwargs["json"] = payload
    response = ORIGINAL_REQUEST(self, method, url, **kwargs)
    if is_send and response.ok:
        try:
            payload = kwargs.get("json") or {}
            text = str(payload.get("text", ""))
            result = response.json().get("result") or {}
            message_id = result.get("message_id")
            if AUTHORIZED and message_id and "SIGNAL" in text and "Expiry" in text:
                am = re.search(r"Asset • ([^\n]+)", text); dm = re.search(r"SIGNAL • (UP|DOWN)", text); em = re.search(r"Expiry • (\d+) min", text); im = re.search(r"Entry • ([0-9.]+)", text); cm = re.search(r"Confidence • (\d+)%", text)
                if am and dm and em and im:
                    asset, expiry = am.group(1).strip(), int(em.group(1))
                    ACTIVE_SIGNALS[asset] = time.time() + expiry * 60
                    threading.Thread(target=_timer, args=(ACTIVE_CHAT, int(message_id), asset, dm.group(1), float(im.group(1)), expiry, int(cm.group(1)) if cm else 0), daemon=True).start()
        except Exception:
            LOG.exception("Telegram timer setup failed")
    return response


requests.sessions.Session.request = _telegram_request


def _scheduler(mod, original_send, outcome_register):
    """Exactly one qualified signal per five-minute window.

    Slot 0 (00-00:59) may use only the previous hour's prepared candidates.
    Slots 1-4 (01-04:59) may use previous-hour candidates plus candidates formed
    during the current window. This matches the requested 20:00-20:04 behavior.
    """
    last_window = None
    while True:
        now = int(time.time())
        window = now // 300
        slot = (now // 60) % 5
        if window != last_window:
            last_window = window
            with LOCK:
                SENT_WINDOWS.discard(window)
            LOG.info("SCAN_WINDOW window=%s slot=0 prior_hour=%s", window, ",".join(str(window-i) for i in range(1, 13)))
        with LOCK:
            auth = AUTHORIZED
            sent = window in SENT_WINDOWS
            previous = [v for v in CANDIDATES.values() if window - 12 <= v["window"] < window]
            current = [v for v in CANDIDATES.values() if v["window"] == window]
        if not auth:
            if slot == 0:
                LOG.info("WINDOW_BLOCKED window=%s reason=telegram_not_authorized", window)
        elif not sent:
            pool = previous if slot == 0 else previous + current
            if pool:
                # Prefer quality, then confidence, then freshest candidate.
                best = max(pool, key=lambda x: (x["quality"], x["confidence"], x.get("created", 0)))
                # Candidate must still have a recent live candle; never send stale/closed-market data.
                try:
                    candle_ts = float(best["data"][-1].get("timestamp", 0) or 0)
                    if candle_ts > 10000000000: candle_ts /= 1000
                    fresh = time.time() - candle_ts <= 90
                except Exception:
                    fresh = False
                if not fresh:
                    LOG.info("WINDOW_SKIP window=%s slot=%s asset=%s reason=stale_candidate", window, slot, best["asset"])
                else:
                    brain = best["tech"].get("brain") or {}
                    expiry = int(brain.get("expiry", best["expiry"]) or best["expiry"])
                    if expiry not in SUPPORTED_EXPIRIES: expiry = 5
                    with LOCK:
                        if window in SENT_WINDOWS:
                            continue
                        SENT_WINDOWS.add(window)
                    try:
                        result = original_send(best["asset"], best["data"], best["tech"], expiry)
                        if result.get("status") == "SIGNAL":
                            payload = dict(result)
                            payload.update({"timestamp": best["data"][-1].get("timestamp", time.time()), "entry": best["data"][-1].get("close"), "strategy": brain.get("strategy", ""), "regime": brain.get("regime", ""), "pattern": brain.get("pattern", "")})
                            try: outcome_register(payload)
                            except Exception: LOG.exception("Outcome registration failed")
                            LOG.info("WINDOW_SIGNAL window=%s slot=%s asset=%s quality=%s confidence=%s expiry=%s source_window=%s", window, slot, best["asset"], best["quality"], best["confidence"], expiry, best["window"])
                        else:
                            with LOCK: SENT_WINDOWS.discard(window)
                            LOG.info("WINDOW_REJECTED window=%s slot=%s asset=%s reason=%s", window, slot, best["asset"], result.get("reason"))
                    except Exception:
                        with LOCK: SENT_WINDOWS.discard(window)
                        LOG.exception("Window signal send failed")
            elif slot in (0, 1, 2, 3, 4):
                LOG.info("WINDOW_WAIT window=%s slot=%s previous=%s current=%s", window, slot, len(previous), len(current))
        with LOCK:
            for key, value in list(CANDIDATES.items()):
                if value["window"] < window - 12:
                    CANDIDATES.pop(key, None)
        time.sleep(1)


def _install():
    while True:
        mod = sys.modules.get("__main__")
        if mod is not None and all(hasattr(mod, name) for name in ("analyze", "on_olymp_candle", "send_signal")):
            original_analyze, original_candle, original_send = mod.analyze, mod.on_olymp_candle, mod.send_signal
            try:
                from strategy_brain import evaluate as brain_evaluate
                from outcome_engine import on_candle as outcome_on_candle, performance as outcome_performance, register as outcome_register
            except Exception:
                LOG.exception("Analyst modules failed"); time.sleep(1); continue

            def gated_analyze(data):
                asset = getattr(threading.current_thread(), "candice_asset", None) or os.getenv("CANDICE_CONTEXT_ASSET", "") or "UNKNOWN"
                base = original_analyze(data)
                try: brain = brain_evaluate(asset, data, base)
                except Exception: brain = {"allow": False, "score": 0, "strategy": "error", "regime": "ERROR", "reasons": ["brain error"]}
                enriched = dict(base or {})
                enriched["brain"] = {"regime": brain.get("regime"), "strategy": brain.get("strategy"), "quality": brain.get("score", 0), "pattern": brain.get("pattern"), "expiry": brain.get("expiry", 5)}
                enriched["reasons"] = list(enriched.get("reasons", []))[-4:] + list(brain.get("reasons", []))[-5:]
                if not brain.get("allow"):
                    enriched["decision"] = "NO_SIGNAL"; return enriched
                bd, dd = base.get("direction") if isinstance(base, dict) else None, brain.get("direction")
                if bd and dd and bd != dd:
                    enriched["decision"] = "NO_SIGNAL"; enriched["reasons"].append("technical direction disagrees with market brain"); return enriched
                enriched["decision"] = "SIGNAL"; enriched["direction"] = dd or bd; enriched["confidence"] = max(int(base.get("confidence", 0) or 0), int(brain.get("score", 0) or 0))
                return enriched

            def wrapped_candle(asset, candle):
                threading.current_thread().candice_asset = str(asset)
                try:
                    result = original_candle(asset, candle)
                    try: outcome_on_candle(asset, candle, getattr(mod, "telegram", None))
                    except Exception: LOG.exception("Outcome evaluation failed")
                    return result
                except Exception:
                    LOG.exception("Candle processing failed asset=%s", asset); return None

            def wrapped_send_signal(asset, data, tech, expiry=5):
                with LOCK:
                    if not AUTHORIZED: return {"status": "NO_SIGNAL", "reason": "Telegram access not authorized"}
                    active = ACTIVE_SIGNALS.get(str(asset))
                    if active and active > time.time(): return {"status": "NO_SIGNAL", "reason": "active signal still within expiry"}
                if not isinstance(tech, dict) or tech.get("status") != "SIGNAL": return {"status": "NO_SIGNAL", "reason": "not a qualified candidate"}
                brain = tech.get("brain") or {}
                adaptive = int(brain.get("expiry", expiry) or expiry)
                if adaptive not in SUPPORTED_EXPIRIES: adaptive = 5
                quality = int(brain.get("quality", 0) or 0)
                window = int(time.time()) // 300
                key = f"{asset}:{window}"
                candidate = {"asset": asset, "data": list(data), "tech": dict(tech), "expiry": adaptive, "quality": quality, "confidence": int(tech.get("confidence", 0) or 0), "window": window, "created": time.time()}
                with LOCK: CANDIDATES[key] = candidate
                return {"status": "SIGNAL", "asset": asset, "direction": tech.get("direction"), "entry": data[-1].get("close"), "expiry": adaptive, "confidence": tech.get("confidence", 0)}

            mod.analyze = gated_analyze
            mod.on_olymp_candle = wrapped_candle
            mod.send_signal = wrapped_send_signal
            if not hasattr(mod, "performance_api"):
                @mod.app.get("/performance")
                def performance_api():
                    try: p = outcome_performance()
                    except Exception: p = {}
                    return mod.jsonify({"ok": True, "outcomes": p, "telegram_authorized": AUTHORIZED, "candidate_count": len(CANDIDATES)})
            threading.Thread(target=_poller, daemon=True, name="candice-telegram-owner").start()
            threading.Thread(target=_scheduler, args=(mod, original_send, outcome_register), daemon=True, name="candice-5m-scheduler").start()
            LOG.info("CANDICE 13.7 installed: exact 5-minute slots + prior-hour ranking + stale-candidate guard + adaptive expiry + READ-ONLY")
            return
        time.sleep(.25)

threading.Thread(target=_install, daemon=True, name="candice-analyst-stack").start()

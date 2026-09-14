"""Startup hooks for safe Telegram transport, strategy gating and outcomes."""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

import requests
from requests import Response

_LOG = logging.getLogger("candice.telegram_transport")
_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
_CONFIGURED_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()
_ACTIVE_CHAT = _CONFIGURED_CHAT
_LOCK = threading.RLock()
_ORIGINAL_REQUEST = requests.sessions.Session.request


def _telegram_request(self, method, url, **kwargs):
    global _ACTIVE_CHAT
    if "api.telegram.org" not in str(url):
        return _ORIGINAL_REQUEST(self, method, url, **kwargs)
    is_updates = str(url).endswith("/getUpdates")
    is_send = str(url).endswith("/sendMessage")
    if is_send and _ACTIVE_CHAT:
        payload = kwargs.get("json")
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["chat_id"] = _ACTIVE_CHAT
            kwargs["json"] = payload
    response = _ORIGINAL_REQUEST(self, method, url, **kwargs)
    try:
        data = response.json()
    except Exception:
        data = {}
    if is_updates and data.get("ok"):
        for update in data.get("result", []):
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id", "")).strip()
            text = str(message.get("text", "")).strip().lower()
            if chat_id and text.startswith("/start"):
                with _LOCK:
                    _ACTIVE_CHAT = chat_id
                _LOG.info("Telegram chat bound from /start: type=%s", chat.get("type", "unknown"))
    if is_send and not response.ok:
        code = data.get("error_code", response.status_code)
        desc = data.get("description", "unknown Telegram API error")
        _LOG.warning("Telegram send failed: code=%s description=%s", code, desc)
    if is_updates and response.status_code == 409:
        safe = Response()
        safe.status_code = 200
        safe._content = b'{"ok":true,"result":[]}'
        safe.headers["Content-Type"] = "application/json"
        safe.url = "https://api.telegram.org/bot<redacted>/getUpdates"
        return safe
    return response


requests.sessions.Session.request = _telegram_request

_SIGNAL_STATE: dict[str, bool] = {}
_CONTEXT = threading.local()


def _install_analyst():
    while True:
        mod = sys.modules.get("__main__")
        if mod is not None and hasattr(mod, "analyze") and hasattr(mod, "on_olymp_candle") and hasattr(mod, "send_signal"):
            original_analyze = mod.analyze
            original_candle = mod.on_olymp_candle
            original_send = mod.send_signal
            try:
                from strategy_brain import evaluate as brain_evaluate
                from outcome_engine import on_candle as outcome_on_candle, performance as outcome_performance, register as outcome_register
            except Exception:
                _LOG.exception("Candice analyst modules failed to load")
                time.sleep(1)
                continue

            def gated_candle(asset, candle):
                _CONTEXT.asset = str(asset)
                try:
                    result = original_candle(asset, candle)
                    try:
                        outcome_on_candle(asset, candle, getattr(mod, "telegram", None))
                    except Exception:
                        _LOG.exception("Outcome evaluation failed asset=%s", asset)
                    return result
                finally:
                    try:
                        del _CONTEXT.asset
                    except AttributeError:
                        pass

            def gated_analyze(data):
                result = original_analyze(data)
                asset = getattr(_CONTEXT, "asset", None)
                if not asset or not isinstance(result, dict):
                    return result
                try:
                    brain = brain_evaluate(asset, data, result)
                except Exception:
                    _LOG.exception("Candice own strategy brain error asset=%s", asset)
                    brain = {"allow": False, "regime": "ERROR", "strategy": "brain_error", "score": 0,
                             "reasons": ["strategy brain error; fail-safe NO_SIGNAL"]}
                enriched = dict(result)
                enriched["brain"] = {"regime": brain.get("regime"), "strategy": brain.get("strategy"), "quality": brain.get("score", 0)}
                enriched["reasons"] = list(result.get("reasons", []))[-5:] + list(brain.get("reasons", []))[-4:]
                if result.get("decision") != "SIGNAL" or not brain.get("allow"):
                    _SIGNAL_STATE[asset] = False
                    enriched["decision"] = "NO_SIGNAL"
                    return enriched
                if _SIGNAL_STATE.get(asset, False):
                    enriched["decision"] = "NO_SIGNAL"
                    enriched["reasons"] = list(enriched.get("reasons", []))[-8:] + ["existing qualified setup still active; waiting for a fresh setup"]
                    return enriched
                _SIGNAL_STATE[asset] = True
                enriched["confidence"] = max(int(enriched.get("confidence", 0) or 0), int(brain.get("score", 0) or 0))
                return enriched

            def wrapped_send_signal(asset, data, tech, expiry=5):
                result = original_send(asset, data, tech, expiry)
                if result.get("status") == "SIGNAL":
                    payload = dict(result)
                    payload["timestamp"] = data[-1].get("timestamp", time.time())
                    payload["entry"] = data[-1].get("close")
                    brain = tech.get("brain") or {}
                    payload["strategy"] = brain.get("strategy", "")
                    payload["regime"] = brain.get("regime", "")
                    outcome_register(payload)
                return result

            mod.analyze = gated_analyze
            mod.on_olymp_candle = gated_candle
            mod.send_signal = wrapped_send_signal
            original_health = getattr(mod, "health", None)
            if original_health:
                def health_with_outcomes():
                    response = original_health()
                    try:
                        payload = response.get_json()
                        payload["outcomes"] = outcome_performance()
                        return mod.jsonify(payload)
                    except Exception:
                        return response
                mod.health = health_with_outcomes
            _LOG.info("CANDICE A-Z analyst installed: own brain + fresh-setup gate + automatic expiry outcome evaluation")
            return
        time.sleep(0.25)


threading.Thread(target=_install_analyst, daemon=True, name="candice-analyst-stack").start()

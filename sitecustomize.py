"""Startup hooks for safe Telegram transport and Candice signal gating."""
from __future__ import annotations

import json
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

    # Never let requests.raise_for_status() print a Telegram URL containing the bot token.
    if is_updates and response.status_code == 409:
        safe = Response()
        safe.status_code = 200
        safe._content = b'{"ok":true,"result":[]}'
        safe.headers["Content-Type"] = "application/json"
        safe.url = "https://api.telegram.org/bot<redacted>/getUpdates"
        return safe

    return response


requests.sessions.Session.request = _telegram_request


# Candice must analyze every completed 1-minute candle, but a qualifying setup
# should create only ONE Telegram signal. A new signal is allowed only after
# the previous qualification disappears (NO_SIGNAL) and a fresh setup forms.
_SIGNAL_STATE: dict[str, bool] = {}
_CONTEXT = threading.local()


def _install_signal_gate():
    while True:
        mod = sys.modules.get("__main__")
        if mod is not None and hasattr(mod, "analyze") and hasattr(mod, "on_olymp_candle"):
            original_analyze = mod.analyze
            original_candle = mod.on_olymp_candle

            def gated_candle(asset, candle):
                _CONTEXT.asset = str(asset)
                try:
                    return original_candle(asset, candle)
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

                decision = result.get("decision")
                active = _SIGNAL_STATE.get(asset, False)

                if decision == "SIGNAL":
                    if active:
                        result = dict(result)
                        result["decision"] = "NO_SIGNAL"
                        result["reasons"] = list(result.get("reasons", []))[-7:] + [
                            "existing qualified setup still active; waiting for a fresh setup"
                        ]
                    else:
                        _SIGNAL_STATE[asset] = True
                else:
                    # The setup has broken; the next qualified setup may signal.
                    _SIGNAL_STATE[asset] = False

                return result

            mod.analyze = gated_analyze
            mod.on_olymp_candle = gated_candle
            _LOG.info("Candice signal transition gate installed: 1m analysis ON, repeated setup alerts OFF")
            return
        time.sleep(0.25)


threading.Thread(target=_install_signal_gate, daemon=True, name="candice-signal-gate").start()

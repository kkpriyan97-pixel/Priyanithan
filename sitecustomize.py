"""Minimal startup hook: fix Telegram delivery without touching trading logic.

This hook only intercepts Telegram Bot API traffic. It learns the real private
chat from /start/getUpdates and uses that chat for outgoing sendMessage calls,
which prevents a stale TELEGRAM_CHAT_ID from causing persistent 403/permission
failures. No bot token or full API URL is ever logged.
"""
from __future__ import annotations

import json
import logging
import os
import threading

import requests

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

    return response


requests.sessions.Session.request = _telegram_request

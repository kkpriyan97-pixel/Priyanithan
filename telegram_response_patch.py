"""Reliable read-only Telegram reply transport for Candice.

Patches the existing Telegram sender only; it does not change authentication,
market logic, account selection, or trading behavior.
"""
from __future__ import annotations

import logging
import sys
import time

import requests

LOG = logging.getLogger("candice.telegram")


def _install():
    # sitecustomize is loaded before user .pth hooks. Wait briefly so its
    # Telegram globals are available, then replace only the transport helper.
    for _ in range(120):
        sc = sys.modules.get("sitecustomize")
        if sc is not None and hasattr(sc, "TOKEN") and hasattr(sc, "SESSION"):
            token = str(getattr(sc, "TOKEN", "") or "").strip()
            if not token:
                LOG.warning("TELEGRAM_RESPONSE_PATCH: token missing")
                return

            def reliable_tg_send(chat, text):
                chat = str(chat or "").strip()
                if not token or not chat:
                    LOG.warning("TELEGRAM_REPLY_BLOCKED missing token/chat")
                    return False
                payload = {
                    "chat_id": chat,
                    "text": str(text),
                    "disable_web_page_preview": True,
                }
                last_error = "unknown"
                for attempt in range(1, 4):
                    try:
                        r = sc.SESSION.post(
                            f"https://api.telegram.org/bot{token}/sendMessage",
                            json=payload,
                            timeout=20,
                        )
                        if r.ok:
                            LOG.info("TELEGRAM_REPLY_SENT chat=%s attempt=%s", chat, attempt)
                            return True
                        try:
                            body = r.json()
                            desc = body.get("description", "unknown")
                        except Exception:
                            desc = r.text[:200] if r.text else "unknown"
                        last_error = f"HTTP {r.status_code}: {desc}"
                        # Retry transient Telegram/server/rate-limit failures.
                        if r.status_code not in (409, 429, 500, 502, 503, 504):
                            break
                    except Exception as exc:
                        last_error = f"{type(exc).__name__}: {exc}"
                    time.sleep(min(2 * attempt, 5))
                LOG.error("TELEGRAM_REPLY_FAILED chat=%s | %s", chat, last_error)
                return False

            sc.tg_send = reliable_tg_send
            LOG.info("TELEGRAM_RESPONSE_PATCH installed | /start and /access replies use retrying transport")
            return
        time.sleep(0.25)
    LOG.error("TELEGRAM_RESPONSE_PATCH could not find sitecustomize")


_install()

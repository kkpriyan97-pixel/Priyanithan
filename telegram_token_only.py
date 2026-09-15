"""Token-only Telegram routing for Candice.

No TELEGRAM_CHAT_ID is required. The bot discovers the destination chat from
an incoming human /start or /access message and keeps the bot's own id blocked.
"""
from __future__ import annotations
import logging
import sys
import threading
import time

LOG = logging.getLogger("candice.telegram")


def _install():
    for _ in range(120):
        sc = sys.modules.get("sitecustomize")
        if sc is not None:
            try:
                # Token is the only Telegram destination configuration.
                # Ignore any stale/incorrect TELEGRAM_CHAT_ID value.
                sc.CONFIGURED_CHAT = ""
                LOG.info("TELEGRAM_TOKEN_ONLY enabled | chat id configuration ignored | destination discovered from human /start or /access")
                return True
            except Exception:
                LOG.exception("TELEGRAM_TOKEN_ONLY install failed")
        time.sleep(0.5)
    LOG.error("TELEGRAM_TOKEN_ONLY could not find sitecustomize")
    return False


threading.Thread(target=_install, name="candice-telegram-token-only", daemon=True).start()

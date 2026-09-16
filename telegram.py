from __future__ import annotations
import logging, os, requests

log = logging.getLogger("candice.telegram")

class Telegram:
    """Telegram delivery using BOT TOKEN only; no chat ID is required."""
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat = ""
        self.api = f"https://api.telegram.org/bot{self.token}" if self.token else ""

    def _discover_chat(self):
        if not self.api:
            return ""
        try:
            r = requests.get(f"{self.api}/getUpdates", params={"limit": 20, "timeout": 1}, timeout=5)
            data = r.json()
            if not data.get("ok"):
                return ""
            for item in reversed(data.get("result", [])):
                msg = item.get("message") or item.get("channel_post")
                if not msg:
                    continue
                chat_id = (msg.get("chat") or {}).get("id")
                if chat_id is not None:
                    self.chat = str(chat_id)
                    log.info("TELEGRAM_CHAT_DISCOVERED")
                    return self.chat
        except Exception as e:
            log.warning("TELEGRAM_DISCOVERY_ERROR %s", type(e).__name__)
        return ""

    def send(self, text):
        if not self.token:
            log.warning("TELEGRAM_NOT_READY token=False")
            return False
        chat = self.chat or self._discover_chat()
        if not chat:
            log.warning("TELEGRAM_WAITING_FOR_CHAT token=True")
            return False
        try:
            r = requests.post(f"{self.api}/sendMessage", json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=15)
            if not r.ok:
                log.warning("TELEGRAM_SEND_FAILED status=%s", r.status_code)
                return False
            return True
        except Exception as e:
            log.warning("TELEGRAM_SEND_ERROR %s", type(e).__name__)
            return False

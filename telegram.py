from __future__ import annotations

import logging
import os
import threading
import time

import requests

log = logging.getLogger("candice.telegram")


class Telegram:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.access_code = os.getenv("CANDICE_ACCESS_CODE", "").strip()
        self.api = f"https://api.telegram.org/bot{self.token}" if self.token else ""
        self.authorized = bool(self.chat)
        self._offset = 0
        self._stop = threading.Event()
        self._poll_thread = None
        self._send_lock = threading.Lock()

    def _post(self, method: str, payload: dict, timeout: int = 15):
        if not self.api:
            return None
        try:
            with self._send_lock:
                r = requests.post(f"{self.api}/{method}", json=payload, timeout=timeout)
            if not r.ok:
                log.warning("TELEGRAM_API_FAILED method=%s status=%s", method, r.status_code)
                return None
            data = r.json()
            return data if data.get("ok") else None
        except Exception as e:
            log.warning("TELEGRAM_API_ERROR method=%s type=%s", method, type(e).__name__)
            return None

    def _send_to(self, chat_id: str, text: str):
        return self._post(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=15,
        )

    def _handle_message(self, message: dict):
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        chat_id = str(chat_id)
        text = (message.get("text") or "").strip()
        if not text:
            return

        if text.startswith("/start"):
            if self.authorized and self.chat == chat_id:
                self._send_to(chat_id, "✅ CANDICE AI is connected.\n📡 Live market engine: ON\n🔒 Read-only mode: ON")
            else:
                self._send_to(chat_id, "👋 CANDICE AI\n\n🔐 Access required.\nUse: /access <your access code>")
            return

        if text.startswith("/access"):
            parts = text.split(maxsplit=1)
            supplied = parts[1].strip() if len(parts) == 2 else ""
            if not self.access_code:
                log.warning("TELEGRAM_ACCESS_CODE_NOT_CONFIGURED")
                self._send_to(chat_id, "⚠️ Access is not configured on the server yet.")
                return
            if supplied != self.access_code:
                log.warning("TELEGRAM_ACCESS_DENIED")
                self._send_to(chat_id, "❌ Invalid access code.")
                return

            self.chat = chat_id
            self.authorized = True
            log.info("TELEGRAM_ACCESS_GRANTED")
            self._send_to(
                chat_id,
                "✅ CANDICE AI ACCESS GRANTED\n\n📡 Live market feed: ON\n🧠 Brain: ON\n🔒 Read-only: ON\n🚫 Auto-trade: OFF\n🚫 Martingale: OFF\n\nWaiting for a qualified signal...",
            )
            return

    def _poll_loop(self):
        log.info("TELEGRAM_COMMAND_POLL_STARTED interval=2s")
        while not self._stop.is_set():
            if not self.api:
                self._stop.wait(2)
                continue
            try:
                params = {"limit": 20, "timeout": 1}
                if self._offset:
                    params["offset"] = self._offset
                with self._send_lock:
                    r = requests.get(f"{self.api}/getUpdates", params=params, timeout=5)
                if not r.ok:
                    log.warning("TELEGRAM_UPDATES_FAILED status=%s", r.status_code)
                    self._stop.wait(2)
                    continue
                data = r.json()
                if not data.get("ok"):
                    log.warning("TELEGRAM_UPDATES_REJECTED")
                    self._stop.wait(2)
                    continue
                for item in data.get("result", []):
                    self._offset = max(self._offset, int(item.get("update_id", 0)) + 1)
                    message = item.get("message") or item.get("channel_post") or {}
                    self._handle_message(message)
            except Exception as e:
                log.warning("TELEGRAM_POLL_ERROR type=%s", type(e).__name__)
                self._stop.wait(2)
        log.info("TELEGRAM_COMMAND_POLL_STOPPED")

    def start(self):
        if not self.token:
            log.warning("TELEGRAM_DISABLED_NO_TOKEN")
            return
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._stop.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="telegram-command-poll")
        self._poll_thread.start()

    def stop(self):
        self._stop.set()

    def send(self, text):
        if not self.token or not self.authorized or not self.chat:
            return False
        data = self._send_to(self.chat, text)
        if not data:
            log.warning("TELEGRAM_SEND_FAILED")
            return False
        result = data.get("result") or {}
        log.info("TELEGRAM_SENT message_id=%s", result.get("message_id"))
        return {"message_id": result.get("message_id"), "chat_id": result.get("chat", {}).get("id")}

    def edit(self, message_id, text):
        if not self.token or not self.chat or not message_id:
            return False
        data = self._post(
            "editMessageText",
            {"chat_id": self.chat, "message_id": message_id, "text": text, "disable_web_page_preview": True},
            timeout=10,
        )
        return bool(data)

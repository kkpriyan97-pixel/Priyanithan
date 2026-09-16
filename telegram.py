from __future__ import annotations

import logging
import os
import threading
import requests

log = logging.getLogger("candice.telegram")

class Telegram:
    def __init__(self):
        raw_token=os.getenv("TELEGRAM_BOT_TOKEN","").strip(); token=raw_token
        if token.startswith("https://api.telegram.org/bot"): token=token[len("https://api.telegram.org/bot"):]
        if token.lower().startswith("bot"): token=token[3:]
        self.token=token.strip(); self.chat=os.getenv("TELEGRAM_CHAT_ID","").strip(); self.access_code=os.getenv("CANDICE_ACCESS_CODE","").strip()
        self.api=f"https://api.telegram.org/bot{self.token}" if self.token else ""
        self.webhook_url=(os.getenv("TELEGRAM_WEBHOOK_URL","").strip() or (os.getenv("RENDER_EXTERNAL_URL","").strip().rstrip("/")+"/telegram/webhook" if os.getenv("RENDER_EXTERNAL_URL") else ""))
        self.authorized=bool(self.chat); self._send_lock=threading.Lock(); self._poll_thread=None; self._stop=threading.Event(); self._offset=0

    def _post(self,method,payload,timeout=15):
        if not self.api:return None
        try:
            with self._send_lock:r=requests.post(f"{self.api}/{method}",json=payload,timeout=timeout)
            if not r.ok:log.warning("TELEGRAM_API_FAILED method=%s status=%s",method,r.status_code);return None
            data=r.json();return data if data.get("ok") else None
        except Exception as e:log.warning("TELEGRAM_API_ERROR method=%s type=%s",method,type(e).__name__);return None

    def _send_to(self,chat_id,text):return self._post("sendMessage",{"chat_id":chat_id,"text":text,"disable_web_page_preview":True},15)

    def _handle_message(self,message):
        chat=message.get("chat") or {}; chat_id=chat.get("id")
        if chat_id is None:return
        chat_id=str(chat_id); text=(message.get("text") or "").strip()
        if not text:return
        if text.startswith("/start"):
            self._send_to(chat_id,"✅ CANDICE AI is connected.\n📡 Live market engine: ON\n🔒 Read-only mode: ON" if self.authorized and self.chat==chat_id else "👋 CANDICE AI\n\n🔐 Access required.\nUse: /access <your access code>")
            return
        if text.startswith("/access"):
            parts=text.split(maxsplit=1); supplied=parts[1].strip() if len(parts)==2 else ""
            if not self.access_code:self._send_to(chat_id,"⚠️ Access is not configured on the server yet.");return
            if supplied!=self.access_code:log.warning("TELEGRAM_ACCESS_DENIED");self._send_to(chat_id,"❌ Invalid access code.");return
            self.chat=chat_id;self.authorized=True;log.info("TELEGRAM_ACCESS_GRANTED");self._send_to(chat_id,"✅ CANDICE AI ACCESS GRANTED\n\n📡 Live market feed: ON\n🧠 Brain: ON\n🔒 Read-only: ON\n🚫 Auto-trade: OFF\n🚫 Martingale: OFF\n\nWaiting for a qualified signal...")

    def configure_webhook(self):
        if not self.api or not self.webhook_url:return False
        data=self._post("setWebhook",{"url":self.webhook_url,"drop_pending_updates":False},15)
        if data:
            log.info("TELEGRAM_WEBHOOK_SET url=%s",self.webhook_url)
            return True
        return False

    def handle_webhook(self,update):
        if not isinstance(update,dict):return False
        self._handle_message(update.get("message") or update.get("channel_post") or {})
        return True

    def start(self):
        if not self.token:log.warning("TELEGRAM_DISABLED_NO_TOKEN");return
        if self.webhook_url and self.configure_webhook():
            log.info("TELEGRAM_COMMAND_WEBHOOK_STARTED")
            return
        # Local/non-Render fallback. A successful webhook never starts polling,
        # avoiding Telegram 409 conflicts between consumers.
        if self._poll_thread and self._poll_thread.is_alive():return
        self._stop.clear();self._poll_thread=threading.Thread(target=self._poll_loop,daemon=True,name="telegram-command-poll");self._poll_thread.start()

    def _poll_loop(self):
        log.info("TELEGRAM_COMMAND_POLL_STARTED interval=2s");self._post("deleteWebhook",{"drop_pending_updates":False},10)
        while not self._stop.is_set():
            if not self.api:self._stop.wait(2);continue
            try:
                params={"limit":20,"timeout":1};
                if self._offset:params["offset"]=self._offset
                with self._send_lock:r=requests.get(f"{self.api}/getUpdates",params=params,timeout=5)
                if r.status_code==409:log.warning("TELEGRAM_UPDATES_CONFLICT waiting=10s");self._stop.wait(10);continue
                if not r.ok:log.warning("TELEGRAM_UPDATES_FAILED status=%s",r.status_code);self._stop.wait(2);continue
                data=r.json()
                if not data.get("ok"):self._stop.wait(2);continue
                for item in data.get("result",[]):
                    self._offset=max(self._offset,int(item.get("update_id",0))+1);self._handle_message(item.get("message") or item.get("channel_post") or {})
            except Exception as e:log.warning("TELEGRAM_POLL_ERROR type=%s",type(e).__name__);self._stop.wait(2)
        log.info("TELEGRAM_COMMAND_POLL_STOPPED")

    def stop(self):self._stop.set()
    def send(self,text):
        if not self.token or not self.authorized or not self.chat:return False
        data=self._send_to(self.chat,text)
        if not data:log.warning("TELEGRAM_SEND_FAILED");return False
        result=data.get("result") or {};log.info("TELEGRAM_SENT message_id=%s",result.get("message_id"));return {"message_id":result.get("message_id"),"chat_id":result.get("chat",{}).get("id")}
    def edit(self,message_id,text):
        if not self.token or not self.chat or not message_id:return False
        return bool(self._post("editMessageText",{"chat_id":self.chat,"message_id":message_id,"text":text,"disable_web_page_preview":True},10))

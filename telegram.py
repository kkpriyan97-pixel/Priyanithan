from __future__ import annotations
import logging,os,requests
log=logging.getLogger("candice.telegram")
class Telegram:
    def __init__(self):self.token=os.getenv("TELEGRAM_BOT_TOKEN","").strip();self.chat=os.getenv("TELEGRAM_CHAT_ID","").strip()
    def send(self,text):
        if not self.token or not self.chat:
            log.warning("TELEGRAM_NOT_READY token=%s chat=%s",bool(self.token),bool(self.chat));return False
        try:
            r=requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",json={"chat_id":self.chat,"text":text,"disable_web_page_preview":True},timeout=15)
            if not r.ok:log.warning("TELEGRAM_SEND_FAILED status=%s",r.status_code)
            return r.ok
        except Exception as e:log.warning("TELEGRAM_SEND_ERROR %s",type(e).__name__);return False

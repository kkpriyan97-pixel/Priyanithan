from __future__ import annotations
import logging,os,requests
log=logging.getLogger("candice.telegram")
class Telegram:
 def __init__(self):
  self.token=os.getenv("TELEGRAM_BOT_TOKEN","").strip();self.chat=os.getenv("TELEGRAM_CHAT_ID","").strip();self.api=f"https://api.telegram.org/bot{self.token}" if self.token else ""
 def _discover_chat(self):
  if not self.api:return ""
  try:
   r=requests.get(f"{self.api}/getUpdates",params={"limit":20,"timeout":1},timeout=5);data=r.json()
   if not data.get("ok"):return ""
   for item in reversed(data.get("result",[])):
    msg=item.get("message") or item.get("channel_post") or {};cid=(msg.get("chat") or {}).get("id")
    if cid is not None:self.chat=str(cid);log.info("TELEGRAM_CHAT_DISCOVERED");return self.chat
  except Exception as e:log.warning("TELEGRAM_DISCOVERY_ERROR %s",type(e).__name__)
  return ""
 def send(self,text):
  if not self.token:return False
  chat=self.chat or self._discover_chat()
  if not chat:return False
  try:
   r=requests.post(f"{self.api}/sendMessage",json={"chat_id":chat,"text":text,"disable_web_page_preview":True},timeout=15)
   if not r.ok:log.warning("TELEGRAM_SEND_FAILED status=%s",r.status_code);return False
   d=r.json().get("result") or {};return {"message_id":d.get("message_id"),"chat_id":d.get("chat",{}).get("id")}
  except Exception as e:log.warning("TELEGRAM_SEND_ERROR %s",type(e).__name__);return False
 def edit(self,message_id,text):
  if not self.token or not self.chat or not message_id:return False
  try:
   r=requests.post(f"{self.api}/editMessageText",json={"chat_id":self.chat,"message_id":message_id,"text":text,"disable_web_page_preview":True},timeout=10)
   return bool(r.ok)
  except Exception as e:log.warning("TELEGRAM_EDIT_ERROR %s",type(e).__name__);return False

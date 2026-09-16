from __future__ import annotations
import logging, os, threading, requests
log=logging.getLogger("candice.telegram")
class Telegram:
 def __init__(self):
  raw=os.getenv("TELEGRAM_BOT_TOKEN","").strip(); token=raw
  if token.startswith("https://api.telegram.org/bot"): token=token[len("https://api.telegram.org/bot"):]
  if token.lower().startswith("bot"): token=token[3:]
  self.token=token.strip(); self.chat=""; self.access_code=os.getenv("CANDICE_ACCESS_CODE","").strip(); self.api=f"https://api.telegram.org/bot{self.token}" if self.token else ""; self.webhook_url=(os.getenv("TELEGRAM_WEBHOOK_URL","").strip() or (os.getenv("RENDER_EXTERNAL_URL","").strip().rstrip("/")+"/telegram/webhook" if os.getenv("RENDER_EXTERNAL_URL") else "")); self.authorized=False; self._send_lock=threading.Lock(); self._poll_thread=None; self._stop=threading.Event(); self._offset=0; self.ready=False; self._status_provider=None
 def configure_status_provider(self,provider): self._status_provider=provider
 def _post(self,method,payload=None,timeout=15):
  if not self.api:return None
  try:
   with self._send_lock:r=requests.post(f"{self.api}/{method}",json=payload or {},timeout=timeout)
   try:data=r.json()
   except Exception:data={}
   if not r.ok or not data.get("ok"):
    desc=str(data.get("description","")).strip()
    if method=="editMessageText" and "message is not modified" in desc.lower():
     log.info("TELEGRAM_EDIT_NOOP message_id=%s",(payload or {}).get("message_id")); return {"ok":True,"result":{}}
    log.warning("TELEGRAM_API_FAILED method=%s status=%s error=%s",method,r.status_code,desc[:180] or "unknown"); return None
   return data
  except Exception as e: log.warning("TELEGRAM_API_ERROR method=%s type=%s",method,type(e).__name__); return None
 def verify(self):
  if not self.token: log.warning("TELEGRAM_DISABLED_NO_TOKEN"); self.ready=False; return False
  me=self._post("getMe",{},10)
  if not me:self.ready=False;return False
  r=me.get("result") or {}; log.info("TELEGRAM_TOKEN_OK bot_id=%s username=%s",r.get("id"),r.get("username",""))
  self.ready=True; return True
 def _send_to(self,chat_id,text): return self._post("sendMessage",{"chat_id":chat_id,"text":text,"disable_web_page_preview":True},15)
 def _report(self):
  try:x=self._status_provider() if self._status_provider else {}
  except Exception:return "⚠️ Runtime report temporarily unavailable."
  x=x or {}; f=x.get("feed") or {}; b=x.get("brain") or {}; a=f.get("account_snapshots") or {}; assets=f.get("assets") or []; scan=b.get("last_scan_assets") or []
  def money(v):
   if isinstance(v,(list,tuple)) and len(v)>=2:return f"{v[0]} {v[1]}"
   return str(v) if v is not None else "NOT AVAILABLE"
  tid=f.get("trader_id") or f.get("traders_id") or "NOT AVAILABLE"
  return ("━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • FULL STATUS\n━━━━━━━━━━━━━━━━━━━━\n" f"⚙️ ENGINE • {str(x.get('candice','unknown')).upper()}\n" f"👤 ACCOUNT MODE • {f.get('account_mode','UNKNOWN')}\n" f"🆔 TRADER ID • {tid}\n" f"💰 DEMO • {money(a.get('demo'))}\n" f"💰 REAL • {money(a.get('real'))}\n\n" f"🟢 FLEX ONLY • {f.get('flex_only',False)}\n" f"📊 FLEX ASSETS DISCOVERED • {len(assets)}\n" f"📡 SUBSCRIBED • {f.get('subscribed',0)}\n" f"📈 TICKS • {f.get('ticks',0)}\n" f"🕐 COMPLETED 1M • {f.get('completed_1m',0)}\n" f"🧠 BRAIN • {'ON' if x.get('engine_ready') else 'STARTING'}\n" f"⏳ PENDING • {b.get('pending',0)}\n" f"🏆 WIN • {b.get('WIN',0)}\n❌ LOSS • {b.get('LOSS',0)}\n➖ TIE • {b.get('TIE',0)}\n" f"🔴 LOSS STREAK • {b.get('consecutive_losses',0)}\n📉 DAILY LOSSES • {b.get('daily_losses',0)}/{b.get('daily_loss_limit',0)}\n" f"🔎 LAST SCAN • {', '.join(scan[:30]) if scan else 'NONE'}\n\n🔒 READ-ONLY • ON\n🚫 AUTO-TRADE • OFF\n🚫 MARTINGALE • OFF\n━━━━━━━━━━━━━━━━━━━━")
 def _handle_message(self,message):
  chat=message.get("chat") or {}; cid=chat.get("id")
  if cid is None:return
  cid=str(cid); text=(message.get("text") or "").strip()
  if not text:return
  cmd=text.split()[0].split("@")[0]
  if cmd=="/start": self._send_to(cid,"✅ CANDICE AI is connected.\n📡 Live market engine: ON\n🔒 Read-only mode: ON" if self.authorized and self.chat==cid else "👋 CANDICE AI\n\n🔐 Access required.\nUse: /access <your access code>"); return
  if cmd=="/access":
   parts=text.split(maxsplit=1); supplied=parts[1].strip() if len(parts)==2 else ""
   if not self.access_code:self._send_to(cid,"⚠️ Access is not configured on the server yet.");return
   if supplied!=self.access_code:log.warning("TELEGRAM_ACCESS_DENIED");self._send_to(cid,"❌ Invalid access code.");return
   self.chat=cid;self.authorized=True;log.info("TELEGRAM_ACCESS_GRANTED");self._send_to(cid,"✅ ACCESS GRANTED\n\n"+self._report());return
  if cmd in {"/report","/status","/account","/assets"}:
   self._send_to(cid,self._report() if self.authorized and self.chat==cid else "🔐 Access required. Use: /access <your access code>")
 def configure_webhook(self):
  if not self.api or not self.webhook_url:return False
  return bool(self._post("setWebhook",{"url":self.webhook_url,"drop_pending_updates":False},15))
 def handle_webhook(self,update):
  if not isinstance(update,dict):return False
  self._handle_message(update.get("message") or update.get("channel_post") or {});return True
 def start(self):
  if not self.verify():return
  if self.webhook_url and self.configure_webhook():log.info("TELEGRAM_COMMAND_WEBHOOK_STARTED");return
  if self._poll_thread and self._poll_thread.is_alive():return
  self._stop.clear();self._poll_thread=threading.Thread(target=self._poll_loop,daemon=True,name="telegram-command-poll");self._poll_thread.start()
 def _poll_loop(self):
  log.info("TELEGRAM_COMMAND_POLL_STARTED interval=2s"); self._post("deleteWebhook",{"drop_pending_updates":False},10)
  while not self._stop.is_set():
   try:
    params={"limit":20,"timeout":1};
    if self._offset:params["offset"]=self._offset
    with self._send_lock:r=requests.get(f"{self.api}/getUpdates",params=params,timeout=5)
    if r.status_code==409:self._stop.wait(10);continue
    if not r.ok:self._stop.wait(2);continue
    data=r.json()
    if data.get("ok"):
     for item in data.get("result",[]):self._offset=max(self._offset,int(item.get("update_id",0))+1);self._handle_message(item.get("message") or item.get("channel_post") or {})
   except Exception as e: log.warning("TELEGRAM_POLL_ERROR type=%s",type(e).__name__);self._stop.wait(2)
 def stop(self):self._stop.set()
 def send(self,text):
  if not self.token: log.warning("TELEGRAM_SEND_BLOCKED reason=no_token"); return False
  if not self.authorized or not self.chat: log.warning("TELEGRAM_SEND_BLOCKED reason=chat_not_authorized"); return False
  data=self._send_to(self.chat,text)
  if not data: log.warning("TELEGRAM_SEND_FAILED reason=api_error"); return False
  result=data.get("result") or {}; mid=result.get("message_id")
  if not mid: log.warning("TELEGRAM_SEND_FAILED reason=no_message_id"); return False
  log.info("TELEGRAM_SENT message_id=%s",mid);return {"message_id":mid,"chat_id":result.get("chat",{}).get("id")}
 def edit(self,message_id,text):
  if not self.token or not self.chat or not message_id:return False
  return bool(self._post("editMessageText",{"chat_id":self.chat,"message_id":message_id,"text":text,"disable_web_page_preview":True},10))

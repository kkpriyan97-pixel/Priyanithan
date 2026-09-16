from __future__ import annotations
import asyncio,json,logging,random,string,time
from collections import defaultdict
import websockets
log=logging.getLogger("candice.olymp")
URI="wss://ws.olymptrade.com/otp?cid_ver=1&cid_app=web%40OlympTrade%402025.2.26123%4026123&cid_device=%40%40desktop&cid_os=windows%4010"
ORIGIN="https://olymptrade.com"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
def uid():return ''.join(random.choice(string.ascii_letters+string.digits) for _ in range(16))
def message(event,data,request_id=None):
 x={"t":2,"e":event,"d":data}
 if request_id:x["uuid"]=request_id
 return json.dumps([x],separators=(",",":"))
class OlympReadOnlyClient:
 """Minimal read-only WebSocket client. No order/trade methods exist."""
 def __init__(self,token):
  self.token=token;self.ws=None;self.running=False;self.auth_invalid=False;self.queue=asyncio.Queue();self.callbacks=defaultdict(list);self.pending={};self.account_mode="UNKNOWN";self.account_balance=None;self.account_currency="";self.assets=set();self._reader_task=None;self._dispatcher_task=None
 def on(self,event,callback):self.callbacks[event].append(callback)
 async def connect(self):
  if self.auth_invalid:raise ConnectionError("OlympTrade access token rejected")
  if self.running and self.ws:return
  headers={"Origin":ORIGIN,"User-Agent":UA,"Cookie":f"access_token={self.token}"}
  try:self.ws=await websockets.connect(URI,extra_headers=headers,ping_interval=15,ping_timeout=10,open_timeout=10,close_timeout=5)
  except TypeError:self.ws=await websockets.connect(URI,additional_headers=headers,ping_interval=15,ping_timeout=10,open_timeout=10,close_timeout=5)
  self.queue=asyncio.Queue();self.running=True;self._reader_task=asyncio.create_task(self._reader());self._dispatcher_task=asyncio.create_task(self._dispatcher());log.info("OLYMP_CONNECTED")
 async def close(self):
  self.running=False;ws=self.ws;self.ws=None
  if ws:
   try:await ws.close()
   except Exception:pass
  for task in (self._reader_task,self._dispatcher_task):
   if task and not task.done():task.cancel()
  self._reader_task=None;self._dispatcher_task=None
  for fut in list(self.pending.values()):
   if not fut.done():fut.set_exception(ConnectionError("OlympTrade WebSocket closed"))
  self.pending.clear()
 async def send(self,event,data,wait=False,timeout=10):
  if not self.ws or not self.running:raise ConnectionError("OlympTrade WebSocket is not connected")
  rid=uid() if wait else None;fut=None
  if rid:fut=asyncio.get_running_loop().create_future();self.pending[rid]=fut
  try:
   await self.ws.send(message(event,data,rid))
   if not fut:return None
   return await asyncio.wait_for(fut,timeout)
  finally:
   if rid:self.pending.pop(rid,None)
 async def _reader(self):
  try:
   while self.running and self.ws:
    raw=await self.ws.recv();await self.queue.put(raw)
  except asyncio.CancelledError:return
  except Exception as e:
   if self.running:
    ws=self.ws;code=getattr(ws,"close_code",None);reason=getattr(ws,"close_reason",None)
    log.warning("OLYMP_SOCKET_STOPPED type=%s code=%s reason=%s",type(e).__name__,code,reason)
    if code==1008 and str(reason).lower()=="invalid_token":self.auth_invalid=True;log.error("OLYMP_AUTH_INVALID token_rejected_by_server")
  finally:
   if self.running:
    self.running=False
    for fut in list(self.pending.values()):
     if not fut.done():fut.set_exception(ConnectionError("OlympTrade WebSocket disconnected"))
    self.pending.clear()
 async def _dispatcher(self):
  try:
   while self.running:
    raw=await self.queue.get();rows=json.loads(raw) if isinstance(raw,str) else raw
    if not isinstance(rows,list):continue
    for msg in rows:
     if not isinstance(msg,dict):continue
     rid=msg.get("uuid")
     if rid in self.pending and not self.pending[rid].done():self.pending[rid].set_result(msg)
     e=msg.get("e")
     callbacks=list(self.callbacks.get(e,[]))+list(self.callbacks.get("*",[]))
     for cb in callbacks:
      try:
       r=cb(msg)
       if asyncio.iscoroutine(r):await r
      except Exception:log.exception("callback failed event=%s",e)
  except asyncio.CancelledError:return
 async def initialize_read_only(self):
  subscriptions=[[220],[110,700,112,140,1038,1037,1039,141,22,26,111],[1054,1076,1301,1097],[141,241],[230,231],[75],[1055],[2223,2301,55,150,152,151,126,602,601],[2076],[126]]
  for sub in subscriptions:
   try:await self.send(98,sub,False)
   except Exception as e:log.debug("SUBSCRIPTION_INIT_FAILED error=%s",type(e).__name__)
  for _ in range(2):
   try:await self.send(90,{},True,5)
   except Exception as e:log.debug("SESSION_INIT_FAILED error=%s",type(e).__name__)
  for group in ("demo","real"):
   try:
    r=await self.send(1068,[{"group":group}],True,8);rows=(r or {}).get("d") or []
    if rows:
     bal=None
     for x in rows:
      if isinstance(x,dict):
       try:bal=float(x.get("amount",x.get("amount_real",x.get("amount_free"))))
       except Exception:bal=None
       if bal is not None:break
     self.account_mode=group.upper();self.account_balance=bal;self.account_currency=str(rows[0].get("currency","") or "");log.info("ACCOUNT_MODE_DETECTED mode=%s balance=%s",self.account_mode,self.account_balance);break
   except Exception as e:log.debug("ACCOUNT_INIT_FAILED group=%s error=%s",group,type(e).__name__)
 async def subscribe_ticks(self,asset):await self.send(12,[{"pair":asset}],False);await self.send(280,[{"pair":asset}],False)
 async def request_candles(self,asset,count=80):
  count=max(60,min(int(count),360));return await self.send(10,[{"pair":asset,"size":count,"to":int(time.time()),"solid":True}],True,12)

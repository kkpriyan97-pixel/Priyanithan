from __future__ import annotations
import asyncio,logging,os,time
from collections import defaultdict,deque
from olymp_client import OlympReadOnlyClient
log=logging.getLogger("candice.feed")
def num(v):
 try:return float(v)
 except (TypeError,ValueError):return None
def normalize(x,asset=""):
 if not isinstance(x,dict):return None
 a=str(x.get("p",x.get("pair",x.get("symbol",asset)))).upper();o=num(x.get("open",x.get("o")));h=num(x.get("high",x.get("h")));l=num(x.get("low",x.get("l")));c=num(x.get("close",x.get("c")));t=num(x.get("t",x.get("timestamp",x.get("time"))))
 if None in (o,h,l,c,t) or not a:return None
 if t>1e10:t/=1000
 return a,{"open":o,"high":h,"low":l,"close":c,"timestamp":t}
def walk_candles(x):
 if isinstance(x,list):
  out=[]
  for y in x:out.extend(walk_candles(y))
  return out
 if not isinstance(x,dict):return []
 if isinstance(x.get("candles"),list):return walk_candles(x["candles"])
 for k in ("d","data","items","result"):
  if isinstance(x.get(k),(list,dict)):
   r=walk_candles(x[k])
   if r:return r
 return [x]
class LiveMarketFeed:
 def __init__(self,on_candle):
  self.on_candle=on_candle;self.token=os.getenv("OLYMPTRADE_ACCESS_TOKEN","").strip();self.client=OlympReadOnlyClient(self.token);self.assets={a.strip().upper() for a in os.getenv("OLYMPTRADE_ASSETS","").split(",") if a.strip()};self.subscribed=set();self.forming={};self.history=defaultdict(lambda:deque(maxlen=360));self.last_completed=defaultdict(float);self.ticks=0;self.candles=0;self.connected=False
 async def start(self):
  if not self.token:raise RuntimeError("OLYMPTRADE_ACCESS_TOKEN is required")
  self.client.on(1,self._tick);self.client.on(1003,self._candle_response);self.client.on(55,self._account);self.client.on(72,self._assets)
  await self.client.connect();self.connected=True;await self.client.initialize_read_only();await self._discover_and_seed();asyncio.create_task(self._poll_loop());log.info("LIVE_MARKET_FEED started | all available assets | 1m candles")
 async def stop(self):
  self.connected=False;await self.client.close()
 async def _subscribe_asset(self,asset):
  if asset in self.subscribed:return
  try:
   await self.client.subscribe_ticks(asset);r=await self.client.request_candles(asset,360);self._consume_history(asset,r);self.subscribed.add(asset);self.assets.add(asset);log.info("ASSET_SUBSCRIBED asset=%s",asset)
  except Exception as e:log.warning("ASSET_SUBSCRIBE_FAILED asset=%s error=%s",asset,type(e).__name__)
 async def _discover_and_seed(self):
  if not self.assets:self.assets.add("ASIA_X")
  for asset in list(self.assets):await self._subscribe_asset(asset)
  log.info("ASSETS_READY count=%s",len(self.assets))
 def _account(self,msg):
  d=msg.get("d") if isinstance(msg,dict) else None
  if not isinstance(d,list):return
  demos=[];reals=[]
  for x in d:
   if not isinstance(x,dict):continue
   g=str(x.get("group","")).lower();b=num(x.get("amount",x.get("amount_real",x.get("amount_free"))))
   if b is None:continue
   (demos if g=="demo" else reals if g=="real" else []).append((b,str(x.get("currency","") or "")))
  if demos and not reals:self.client.account_mode="DEMO";self.client.account_balance=max(demos)[0]
  elif reals and not demos:self.client.account_mode="REAL";self.client.account_balance=max(reals)[0]
  elif demos and reals:self.client.account_mode="AMBIGUOUS";self.client.account_balance=None
 async def _assets(self,msg):
  found=set()
  def visit(x):
   if isinstance(x,dict):
    for k,v in x.items():
     if str(k).lower() in {"pair","symbol","asset","instrument"}:
      for z in (v if isinstance(v,list) else [v]):
       s=str(z.get("id",z.get("pair",z.get("symbol","")))) if isinstance(z,dict) else str(z);s=s.upper().strip()
       if 3<=len(s)<=30 and s.replace("_","").isalnum():found.add(s)
     else:visit(v)
   elif isinstance(x,list):
    for z in x:visit(z)
  visit(msg.get("d") if isinstance(msg,dict) else None);new=found-self.assets;self.assets.update(found)
  for asset in new:await self._subscribe_asset(asset)
  if found:log.info("ASSET_DISCOVERY found=%s total=%s",len(found),len(self.assets))
 def _consume_history(self,asset,msg):
  for x in walk_candles(msg):
   p=normalize(x,asset)
   if p and p[0]==asset:self.history[asset].append(p[1])
  self.history[asset]=deque(sorted(self.history[asset],key=lambda z:z["timestamp"])[-360:],maxlen=360)
 async def _candle_response(self,msg):
  for x in walk_candles(msg):
   p=normalize(x)
   if not p:continue
   a,c=p;self.assets.add(a);self.history[a].append(c);self.history[a]=deque(sorted(self.history[a],key=lambda z:z["timestamp"])[-360:],maxlen=360);t=c["timestamp"];completed=int(time.time()//60)*60-60
   if t<=completed and t>self.last_completed[a]:self.last_completed[a]=t;self.candles+=1;self.on_candle(a,c)
 async def _tick(self,msg):
  rows=msg.get("d",[]) if isinstance(msg,dict) else []
  if isinstance(rows,dict):rows=[rows]
  for x in rows if isinstance(rows,list) else []:
   if not isinstance(x,dict):continue
   a=str(x.get("p",x.get("pair",x.get("symbol","")))).upper();price=num(x.get("q",x.get("price",x.get("close"))));t=num(x.get("t",x.get("timestamp",x.get("time"))))
   if not a or price is None or t is None:continue
   if t>1e10:t/=1000
   self.assets.add(a);self.ticks+=1;bucket=int(t//60)*60;cur=self.forming.get(a)
   if cur is None or cur["timestamp"]!=bucket:
    if cur and cur["timestamp"]>self.last_completed[a]:self.last_completed[a]=cur["timestamp"];self.candles+=1;self.on_candle(a,dict(cur))
    cur={"open":price,"high":price,"low":price,"close":price,"timestamp":float(bucket)};self.forming[a]=cur
   else:cur["high"]=max(cur["high"],price);cur["low"]=min(cur["low"],price);cur["close"]=price
 async def _poll_loop(self):
  while self.connected:
   for a in list(self.assets):
    try:self._consume_history(a,await self.client.request_candles(a,360))
    except Exception:pass
   await asyncio.sleep(60)
 def snapshot(self,asset):return list(self.history.get(asset,()))
 def live_price(self,asset):
  x=self.forming.get(asset);return float(x["close"]) if x else None
 def status(self):return {"connected":self.connected,"assets":sorted(self.assets),"subscribed":len(self.subscribed),"ticks":self.ticks,"completed_1m":self.candles,"account_mode":self.client.account_mode,"account_balance":self.client.account_balance,"account_currency":self.client.account_currency}

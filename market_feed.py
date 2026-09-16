from __future__ import annotations
import asyncio, logging, os, time
from collections import defaultdict, deque
from olymp_client import OlympReadOnlyClient
log = logging.getLogger("candice.feed")
FLEX_ONLY = os.getenv("OLYMPTRADE_FLEX_ONLY", "1").strip().lower() not in {"0","false","no","off"}

def num(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def normalize(x, asset=""):
    if not isinstance(x, dict): return None
    a = str(x.get("p", x.get("pair", x.get("symbol", asset)))).upper().strip()
    o=num(x.get("open",x.get("o"))); h=num(x.get("high",x.get("h"))); l=num(x.get("low",x.get("l"))); c=num(x.get("close",x.get("c"))); t=num(x.get("t",x.get("timestamp",x.get("time"))))
    if None in (o,h,l,c,t) or not a: return None
    if t>1e10: t/=1000
    return a,{"open":o,"high":h,"low":l,"close":c,"timestamp":t}

def walk_candles(x):
    if isinstance(x,list):
        out=[]
        for y in x: out.extend(walk_candles(y))
        return out
    if not isinstance(x,dict): return []
    if isinstance(x.get("candles"),list): return walk_candles(x["candles"])
    for k in ("d","data","items","result"):
        if isinstance(x.get(k),(list,dict)):
            r=walk_candles(x[k])
            if r:return r
    return [x]

class LiveMarketFeed:
    def __init__(self,on_candle):
        self.on_candle=on_candle; self.token=os.getenv("OLYMPTRADE_ACCESS_TOKEN","").strip(); self.client=OlympReadOnlyClient(self.token)
        self.assets=set() if FLEX_ONLY else {a.strip().upper() for a in os.getenv("OLYMPTRADE_ASSETS","").split(",") if a.strip()}; self.subscribed=set(); self.forming={}; self.history=defaultdict(lambda:deque(maxlen=360)); self.last_completed=defaultdict(float)
        self.ticks=0; self.candles=0; self.connected=False; self._reconnect_task=None; self._poll_task=None; self._last_poll_log=0; self._asset_tasks={}
    @staticmethod
    def _current_bucket(): return int(time.time()//60)*60
    @staticmethod
    def _completed_bucket(ts):
        bucket=int(float(ts)//60)*60; return bucket if bucket<int(time.time()//60)*60 else None
    async def start(self):
        if not self.token: raise RuntimeError("OLYMPTRADE_ACCESS_TOKEN is required")
        self.client.on(1,self._tick); self.client.on(1003,self._candle_response); self.client.on(55,self._account)
        await self._connect_and_seed(); self._reconnect_task=asyncio.create_task(self._connection_watch()); self._poll_task=asyncio.create_task(self._poll_loop()); log.info("LIVE_MARKET_FEED started | 1m candles | READ_ONLY | FLEX_ONLY=%s",FLEX_ONLY)
    async def stop(self):
        self.connected=False
        for task in (self._reconnect_task,self._poll_task):
            if task and not task.done(): task.cancel()
        for task in list(self._asset_tasks.values()):
            if not task.done(): task.cancel()
        await self.client.close()
    async def _connect_and_seed(self):
        await self.client.connect(); self.connected=True; await self.client.initialize_read_only(); self.subscribed.clear(); await self._discover_and_seed()
    async def _connection_watch(self):
        log.info("FEED_CONNECTION_WATCH_STARTED")
        delay=2
        while self.connected:
            if self.client.auth_invalid:
                self.connected=False; self.subscribed.clear(); log.error("OLYMP_RECONNECT_STOPPED reason=invalid_token"); break
            if not self.client.running:
                log.warning("OLYMP_RECONNECT_START delay=%ss",delay); self.subscribed.clear()
                try:
                    await asyncio.sleep(delay)
                    if not self.connected or self.client.auth_invalid: break
                    await self._connect_and_seed(); log.info("OLYMP_RECONNECTED assets=%s subscribed=%s",len(self.assets),len(self.subscribed)); delay=2
                except asyncio.CancelledError:return
                except Exception as e:log.warning("OLYMP_RECONNECT_FAILED error=%s",type(e).__name__); delay=min(delay*2,30)
            else: delay=2
            await asyncio.sleep(1)
    async def _subscribe_asset(self,asset):
        if asset in self.subscribed:return
        try:
            await self.client.subscribe_ticks(asset); response=await self.client.request_candles(asset,360); self._consume_history(asset,response,seed=True); self.subscribed.add(asset); self.assets.add(asset); log.info("ASSET_SUBSCRIBED asset=%s history=%s",asset,len(self.history[asset]))
        except Exception as e:log.warning("ASSET_SUBSCRIBE_FAILED asset=%s error=%s",asset,type(e).__name__)
        finally:self._asset_tasks.pop(asset,None)
    def schedule_asset(self,asset):
        asset=str(asset).upper().strip()
        if not asset or asset in self.subscribed or asset in self._asset_tasks:return
        self.assets.add(asset); self._asset_tasks[asset]=asyncio.create_task(self._subscribe_asset(asset))
    async def _discover_and_seed(self):
        for asset in list(self.assets): self.schedule_asset(asset)
        if self.assets: log.info("ASSETS_CONFIGURED count=%s assets=%s flex_only=%s",len(self.assets),sorted(self.assets),FLEX_ONLY)
        else: log.info("ASSETS_WAITING_FOR_FLEX_DISCOVERY")
    def _account(self,msg):
        d=msg.get("d") if isinstance(msg,dict) else None
        if not isinstance(d,list):return
        demos=[]; reals=[]
        for x in d:
            if not isinstance(x,dict):continue
            group=str(x.get("group","")).lower(); balance=num(x.get("amount",x.get("amount_real",x.get("amount_free"))))
            if balance is None:continue
            item=(balance,str(x.get("currency","") or ""))
            if group=="demo":demos.append(item)
            elif group=="real":reals.append(item)
        self.client.account_snapshots={"demo":max(demos) if demos else None,"real":max(reals) if reals else None}
        if demos and reals:self.client.account_mode="BOTH"
        elif demos:self.client.account_mode="DEMO"
        elif reals:self.client.account_mode="REAL"
        else:self.client.account_mode="UNKNOWN"
        selected=(reals or demos)
        if selected:self.client.account_balance,self.client.account_currency=max(selected)
        log.info("ACCOUNT_SNAPSHOT demo=%s real=%s mode=%s",self.client.account_snapshots.get("demo"),self.client.account_snapshots.get("real"),self.client.account_mode)
    def _put_candle(self,asset,candle,notify=False):
        bucket=int(float(candle["timestamp"])//60)*60; candle=dict(candle); candle["timestamp"]=float(bucket); old={x["timestamp"]:x for x in self.history[asset]}; old[bucket]=candle; self.history[asset]=deque(sorted(old.values(),key=lambda z:z["timestamp"])[-360:],maxlen=360)
        if notify and bucket>self.last_completed[asset]:
            self.last_completed[asset]=float(bucket); self.candles+=1; log.info("CANDLE_COMPLETED asset=%s timestamp=%s source=feed",asset,bucket)
            try:self.on_candle(asset,dict(candle))
            except Exception:log.exception("CANDLE_CALLBACK_FAILED asset=%s",asset)
    def _consume_history(self,asset,msg,seed=False):
        rows=[]
        for x in walk_candles(msg):
            p=normalize(x,asset)
            if p and p[0]==asset:rows.append(p[1])
        if not rows:return
        for candle in sorted(rows,key=lambda z:z["timestamp"]):self._put_candle(asset,candle,notify=False)
        completed=[c for c in rows if self._completed_bucket(c["timestamp"]) is not None]
        if completed and (seed or self.last_completed[asset]==0):self.last_completed[asset]=float(max(int(c["timestamp"]//60)*60 for c in completed))
    async def _candle_response(self,msg):
        for x in walk_candles(msg):
            p=normalize(x)
            if not p:continue
            asset,candle=p
            if FLEX_ONLY and asset not in self.subscribed:continue
            self.assets.add(asset); bucket=self._completed_bucket(candle["timestamp"])
            if bucket is None:continue
            candle["timestamp"]=float(bucket); self._put_candle(asset,candle,notify=True)
    async def _tick(self,msg):
        rows=msg.get("d",[]) if isinstance(msg,dict) else []
        if isinstance(rows,dict):rows=[rows]
        for x in rows if isinstance(rows,list) else []:
            if not isinstance(x,dict):continue
            asset=str(x.get("p",x.get("pair",x.get("symbol","")))).upper().strip(); price=num(x.get("q",x.get("price",x.get("close")))); ts=num(x.get("t",x.get("timestamp",x.get("time"))))
            if not asset or price is None or ts is None:continue
            if FLEX_ONLY and asset not in self.subscribed:continue
            if ts>1e10:ts/=1000
            self.assets.add(asset); self.ticks+=1; bucket=int(ts//60)*60; cur=self.forming.get(asset)
            if cur is None or cur["timestamp"]!=bucket:
                if cur and cur["timestamp"]>self.last_completed[asset]:self._put_candle(asset,cur,notify=True)
                self.forming[asset]={"open":price,"high":price,"low":price,"close":price,"timestamp":float(bucket)}
            else:
                cur["high"]=max(cur["high"],price);cur["low"]=min(cur["low"],price);cur["close"]=price
    async def _poll_loop(self):
        log.info("HISTORY_POLL_STARTED interval=2s")
        while self.connected:
            try:
                if not self.client.running:
                    await asyncio.sleep(2); continue
                current_bucket=self._current_bucket()
                for asset in list(self.subscribed):
                    try:
                        before=self.last_completed[asset]; response=await self.client.request_candles(asset,360); self._consume_history(asset,response,seed=False); history=self.history.get(asset)
                        if not history:continue
                        completed=[c for c in history if float(c["timestamp"])<current_bucket]
                        if not completed:continue
                        latest=max(float(c["timestamp"]) for c in completed)
                        if latest>before:
                            candle=next(c for c in completed if float(c["timestamp"])==latest); self.last_completed[asset]=latest; self.candles+=1; log.info("CANDLE_COMPLETED asset=%s timestamp=%s source=history_poll",asset,int(latest)); self.on_candle(asset,dict(candle))
                    except Exception as e:log.warning("HISTORY_REFRESH_FAILED asset=%s error=%s",asset,type(e).__name__)
                now=time.time()
                if now-self._last_poll_log>=30:
                    self._last_poll_log=now; log.info("FEED_HEARTBEAT connected=%s running=%s ticks=%s completed_1m=%s assets=%s subscribed=%s account_mode=%s flex_only=%s",self.connected,self.client.running,self.ticks,self.candles,len(self.assets),len(self.subscribed),self.client.account_mode,FLEX_ONLY)
                await asyncio.sleep(2)
            except asyncio.CancelledError:return
            except Exception as e:log.exception("POLL_LOOP_ERROR error=%s",type(e).__name__);await asyncio.sleep(2)
    def snapshot(self,asset):return list(self.history.get(asset,()))
    def live_price(self,asset):
        cur=self.forming.get(asset)
        if cur:return float(cur["close"])
        history=self.history.get(asset)
        return float(history[-1]["close"]) if history else None
    def status(self):
        return {"connected":self.connected and self.client.running,"auth_invalid":self.client.auth_invalid,"assets":sorted(self.assets),"subscribed":len(self.subscribed),"ticks":self.ticks,"completed_1m":self.candles,"history_1m":{a:len(self.history[a]) for a in sorted(self.assets)},"account_mode":self.client.account_mode,"account_balance":self.client.account_balance,"account_currency":self.client.account_currency,"account_snapshots":getattr(self.client,"account_snapshots",{"demo":None,"real":None}),"flex_only":FLEX_ONLY}

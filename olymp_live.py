from __future__ import annotations
import asyncio,json,logging,os,re,subprocess,sys,threading,time,urllib.parse,urllib.request
from pathlib import Path
from typing import Callable
log=logging.getLogger("candice.olymp_live")
UPSTREAM="https://github.com/ChipaDevTeam/OlympTradeAPI.git"; LOCAL_API=Path("/tmp/candice_olymptrade_api")

def _clear_telegram_webhook():
    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    if not token:return
    try:
        u=f"https://api.telegram.org/bot{token}/deleteWebhook";d=urllib.parse.urlencode({"drop_pending_updates":"false"}).encode();urllib.request.urlopen(urllib.request.Request(u,data=d,method="POST"),timeout=8).close();log.info("Telegram polling startup: stale webhook cleared")
    except Exception as e:log.warning("Telegram webhook cleanup failed: %s",type(e).__name__)
_clear_telegram_webhook()

def _load():
    target=LOCAL_API/"olymptrade_ws"
    if not target.exists():subprocess.run(["git","clone","--depth","1",UPSTREAM,str(LOCAL_API)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=60)
    if str(LOCAL_API) not in sys.path:sys.path.insert(0,str(LOCAL_API))
    from olymptrade_ws.core.client import OlympTradeClient
    from olymptrade_ws.olympconfig import parameters
    return OlympTradeClient,parameters

def _num(v):
    try:return float(v)
    except (TypeError,ValueError):return None

def _rows(value):
    if isinstance(value,list):
        out=[]
        for x in value:out.extend(_rows(x))
        return out
    if not isinstance(value,dict):return []
    candles=value.get("candles")
    if isinstance(candles,list):
        parent=value.get("p",value.get("pair",value.get("symbol")));out=[]
        for x in candles:
            if isinstance(x,dict) and parent and not any(k in x for k in ("p","pair","symbol")):x={**x,"p":parent}
            out.extend(_rows(x))
        return out
    for k in ("data","d","items","result"):
        x=value.get(k)
        if isinstance(x,(list,dict)):
            r=_rows(x)
            if r:return r
    return [value]
_ASSET_RE=re.compile(r"^[A-Z0-9]{3,30}(?:_[A-Z0-9]+)*$");_ASSET_KEYS={"p","pair","symbol","asset","instrument","instrument_id"};_COLLECTION_KEYS={"assets","pairs","instruments","symbols","markets"}

def _extract_assets(value):
    found=set()
    def walk(v):
        if isinstance(v,dict):
            for k,x in v.items():
                lk=str(k).lower()
                if lk in _ASSET_KEYS:
                    vals=x if isinstance(x,list) else [x]
                    for z in vals:
                        if isinstance(z,dict):
                            for kk in ("p","pair","symbol","asset","id"):
                                s=str(z.get(kk,"")).upper().strip()
                                if _ASSET_RE.fullmatch(s):found.add(s)
                        else:
                            s=str(z).upper().strip()
                            if _ASSET_RE.fullmatch(s):found.add(s)
                else:walk(x)
        elif isinstance(v,list):
            for x in v:walk(x)
    walk(value);return found

def _extract_records(value):
    out={}
    def walk(v):
        if isinstance(v,dict):
            ident=str(v.get("id",v.get("pair",v.get("symbol","")))).upper().strip()
            if _ASSET_RE.fullmatch(ident) and any(k in v for k in ("locked","locked_trading","time_open_trading","time_close_trading")):
                out[ident]={"locked":bool(v.get("locked",False)),"locked_trading":bool(v.get("locked_trading",False)),"time_open_trading":_num(v.get("time_open_trading")),"time_close_trading":_num(v.get("time_close_trading")),"group":str(v.get("group","")).lower()}
            for x in v.values():walk(x)
        elif isinstance(v,list):
            for x in v:walk(x)
    walk(value);return out

def _otc_variant(asset,state):
    a=str(asset).upper()
    if a.endswith("_OTC"):return None
    group=str((state or {}).get("group","")).lower()
    return a+"_OTC" if group in {"currency","goods"} or a in {"XAUUSD","XAGUSD"} else None

class OlympLiveFeed:
    METADATA_EVENTS=(220,110,700,112,140,1038,1037,1039,141,22,26,111,1054,1076,1301,1097,241,230,231,75,1055,2223,2301,55,150,152,151,126,602,601,2076)
    def __init__(self,on_candle:Callable[[str,dict],None],on_history:Callable[[str,dict],None]|None=None):
        self.on_candle=on_candle;self.on_history=on_history;self.token=os.getenv("OLYMPTRADE_ACCESS_TOKEN","").strip();self.assets=list(dict.fromkeys(x.strip().upper() for x in os.getenv("OLYMPTRADE_ASSETS","ASIA_X").split(",") if x.strip()));self.configured_assets=list(self.assets);self.discovered_assets=set();self.market_state={};self.enabled=bool(self.token);self.connected=False;self.thread=None;self.client=None;self.forming={};self.last_emitted={};self.tick_count=0;self.candle_count=0;self.reconnect_count=0;self.last_tick_time=0.0;self.last_candle_time=0.0;self._stop=threading.Event()
    def start(self):
        if not self.enabled:log.warning("Olymp live feed disabled: token not configured");return
        if self.thread and self.thread.is_alive():return
        self._stop.clear();self.thread=threading.Thread(target=self._run,name="candice-olymp-feed",daemon=True);self.thread.start()
    def _run(self):
        backoff=2
        while not self._stop.is_set():
            try:asyncio.run(self._main());backoff=2
            except Exception:log.exception("Olymp live feed stopped unexpectedly")
            self.connected=False
            if self._stop.is_set():break
            self.reconnect_count+=1;wait=min(60,backoff);log.warning("OLYMP_RECONNECT attempt=%s wait=%ss",self.reconnect_count,wait);self._stop.wait(wait);backoff=min(60,backoff*2)
    def _tradeable(self,asset):
        a=str(asset).upper();st=self.market_state.get(a)
        if a.endswith("_OTC"):return not(st and(st.get("locked") or st.get("locked_trading")))
        if not st or st.get("locked") or st.get("locked_trading"):return False
        op,cl=st.get("time_open_trading"),st.get("time_close_trading");now=time.time()
        return not(op is not None and cl is not None and cl>op and not(op<=now<=cl))
    async def _poll_candles(self,assets):
        for i in range(0,len(assets),12):
            batch=assets[i:i+12]
            try:await self.client.send_request(10,[{"pair":a,"size":60,"to":int(time.time()),"solid":True} for a in batch],requires_response=False)
            except Exception as e:log.debug("candle poll failed batch=%s error=%s",len(batch),type(e).__name__)
            await asyncio.sleep(.08)
    async def _candle_response(self,message):
        try:
            parsed=[];now=time.time();completed=(int(now)//60)*60-60
            for x in _rows(message):
                asset=str(x.get("p",x.get("pair",x.get("symbol","")))).upper();o,h,l,c=_num(x.get("open",x.get("o"))),_num(x.get("high",x.get("h"))),_num(x.get("low",x.get("l"))),_num(x.get("close",x.get("c")));t=_num(x.get("t",x.get("timestamp",x.get("time"))))
                if not asset or None in(o,h,l,c,t) or not self._tradeable(asset):continue
                if t>1e10:t/=1000
                if h<max(o,c) or l>min(o,c) or h<l:continue
                parsed.append((asset,t,{"open":o,"high":h,"low":l,"close":c,"timestamp":t}))
            for asset,t,candle in parsed:
                if self.on_history:self.on_history(asset,candle)
                if t>completed or t<=self.last_emitted.get(asset,0):continue
                self.last_emitted[asset]=t;self.candle_count+=1;self.last_candle_time=time.time();self.on_candle(asset,candle);log.info("REAL_1M_CANDLE asset=%s ts=%s close=%s",asset,int(t),candle["close"])
        except Exception:log.exception("Olymp candle response parsing failed")
    async def _tick_response(self,message):
        rows=message.get("d",[]) if isinstance(message,dict) else []
        if isinstance(rows,dict):rows=[rows]
        for x in rows if isinstance(rows,list) else []:
            if not isinstance(x,dict):continue
            asset=str(x.get("p",x.get("pair",x.get("symbol","")))).upper();price=_num(x.get("q",x.get("price",x.get("close"))));ts=_num(x.get("t",x.get("timestamp",x.get("time"))))
            if not asset or price is None or ts is None or asset not in self.assets or not self._tradeable(asset):continue
            if ts>1e10:ts/=1000
            self.tick_count+=1;self.last_tick_time=time.time();bucket=int(ts//60)*60;cur=self.forming.get(asset)
            if cur is None or cur["timestamp"]!=bucket:
                if cur is not None and cur["timestamp"]>self.last_emitted.get(asset,0):self.last_emitted[asset]=cur["timestamp"];self.candle_count+=1;self.last_candle_time=time.time();self.on_candle(asset,cur)
                cur={"open":price,"high":price,"low":price,"close":price,"timestamp":float(bucket)};self.forming[asset]=cur
            else:cur["high"]=max(cur["high"],price);cur["low"]=min(cur["low"],price);cur["close"]=price
    async def _metadata(self,message):
        try:
            rec=_extract_records(message)
            if rec:self.market_state.update(rec)
            found=_extract_assets(message);candidates=set()
            for a in found:
                if self._tradeable(a):candidates.add(a)
                otc=_otc_variant(a,self.market_state.get(a))
                if otc:candidates.add(otc)
            new=candidates-set(self.assets)
            if new:self.discovered_assets.update(new);self.assets.extend(sorted(new));log.info("OLYMP_ASSETS_DISCOVERED count=%s new=%s",len(self.assets),sorted(new))
            for a in sorted(new):
                if self._tradeable(a):
                    try:await self.client.market.subscribe_ticks(a)
                    except Exception:pass
        except Exception:log.exception("Olymp asset metadata parsing failed")
    async def _main(self):
        Client,parameters=_load();self.client=Client(access_token=self.token);self.client.register_callback(parameters.E_TICK_UPDATE,self._tick_response);self.client.register_callback(10,self._candle_response)
        for e in self.METADATA_EVENTS:self.client.register_callback(e,self._metadata)
        await self.client.start();self.connected=True;log.info("OLYMP_CONNECTED reconnect=%s assets=%s",self.reconnect_count,len(self.assets))
        try:await self.client.initialize_session();log.info("Olymp session initialization completed; market metadata requested")
        except Exception:log.exception("Olymp session initialization failed; continuing")
        deadline=time.time()+20
        while time.time()<deadline and not self.market_state and not self._stop.is_set():await asyncio.sleep(1)
        for a in list(self.assets):
            if self._tradeable(a):
                try:await self.client.market.subscribe_ticks(a)
                except Exception:pass
        last_poll=0.0
        while not self._stop.is_set():
            if time.time()-last_poll>=60:
                tradeable=[a for a in self.assets if self._tradeable(a)];await self._poll_candles(tradeable);last_poll=time.time();log.info("CANDLE_POLL assets=%s tradeable=%s",len(self.assets),len(tradeable))
            await asyncio.sleep(1)
        try:await self.client.stop()
        except Exception:pass
    def status(self):
        return {"configured":self.enabled,"connected":self.connected,"assets":self.assets,"configured_assets":self.configured_assets,"discovered_assets":sorted(self.discovered_assets),"tradeable_assets":sorted(a for a in self.assets if self._tradeable(a)),"market_state_count":len(self.market_state),"asset_discovery":"session_metadata_plus_otc_variants","mode":"READ_ONLY_MARKET_DATA","timeframe":"1m_real_candles","auto_trade":False,"ticks_received":self.tick_count,"candles_completed":self.candle_count,"reconnect_count":self.reconnect_count,"last_tick_age":time.time()-self.last_tick_time if self.last_tick_time else None,"last_candle_age":time.time()-self.last_candle_time if self.last_candle_time else None}

from __future__ import annotations
import asyncio, json, logging, os, subprocess, sys, threading, urllib.parse, urllib.request
from pathlib import Path
from typing import Callable

log=logging.getLogger('candice.olymp_live')
UPSTREAM='https://github.com/ChipaDevTeam/OlympTradeAPI.git'
LOCAL_API=Path('/tmp/candice_olymptrade_api')

# Telegram command mode uses getUpdates polling. Clear only a stale webhook;
# never include the bot token in logs.
def _clear_telegram_webhook():
    token=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
    if not token:return
    try:
        url=f'https://api.telegram.org/bot{token}/deleteWebhook'
        data=urllib.parse.urlencode({'drop_pending_updates':'false'}).encode()
        with urllib.request.urlopen(urllib.request.Request(url,data=data,method='POST'),timeout=8) as r:
            ok=bool(json.loads(r.read().decode()).get('ok'))
        log.info('Telegram polling startup: stale webhook cleared=%s',ok)
    except Exception as e:
        log.warning('Telegram webhook cleanup failed (token not logged): %s',type(e).__name__)

_clear_telegram_webhook()

def _load():
    target=LOCAL_API/'olymptrade_ws'
    if not target.exists():
        subprocess.run(['git','clone','--depth','1',UPSTREAM,str(LOCAL_API)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=60)
    sys.path.insert(0,str(LOCAL_API))
    from olymptrade_ws.core.client import OlympTradeClient
    from olymptrade_ws.olympconfig import parameters
    return OlympTradeClient,parameters

def _num(v):
    try:return float(v)
    except (TypeError,ValueError):return None

def _rows(value):
    """Flatten common Olymp candle response shapes into candidate rows."""
    if isinstance(value,list):
        out=[]
        for item in value: out.extend(_rows(item))
        return out
    if not isinstance(value,dict):return []
    for key in ('candles','data','d','items','result'):
        val=value.get(key)
        if isinstance(val,(list,dict)):
            nested=_rows(val)
            if nested:return nested
    return [value]

class OlympLiveFeed:
    '''Read-only Olymp market feed; builds 1m candles from live ticks.'''
    def __init__(self,on_candle:Callable[[str,dict],None],on_history:Callable[[str,dict],None]|None=None):
        self.on_candle=on_candle;self.on_history=on_history;self.token=os.getenv('OLYMPTRADE_ACCESS_TOKEN','').strip();self.assets=[x.strip().upper() for x in os.getenv('OLYMPTRADE_ASSETS','ASIA_X').split(',') if x.strip()];self.enabled=bool(self.token);self.connected=False;self.thread=None;self.client=None;self.forming={};self.tick_count=0
    def start(self):
        if not self.enabled:log.warning('Olymp live feed disabled: token not configured');return
        if self.thread and self.thread.is_alive():return
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()
    def _run(self):
        try:asyncio.run(self._main())
        except Exception:log.exception('Olymp live feed stopped');self.connected=False
    async def _main(self):
        try:Client,p=_load()
        except Exception:log.exception('Unable to load OlympTrade API source');return
        self.client=Client(access_token=self.token)
        async def tick_cb(message):await self._ticks(message)
        self.client.register_callback(p.E_TICK_UPDATE,tick_cb)
        try:
            await self.client.start();self.connected=True;log.info('Olymp live market connection established; assets=%s',self.assets)
            for asset in self.assets:
                try:
                    history=await self.client.market.get_candles(asset,size=60,count=100)
                    seeded=await self._history(history or [],asset)
                    log.info('HISTORY_SEEDED asset=%s count=%s',asset,seeded)
                except Exception:
                    log.exception('Failed to load Olymp candle history: %s',asset)
                try:
                    await self.client.market.subscribe_ticks(asset);log.info('Subscribed to Olymp live ticks: %s',asset)
                except Exception:log.exception('Failed to subscribe to Olymp live ticks: %s',asset)
            while self.client.connection.is_connected:await asyncio.sleep(15)
        except Exception:log.exception('Olymp live market connection error')
        finally:
            self.connected=False
            try:await self.client.stop()
            except Exception:pass
    async def _history(self,h,default_asset=''):
        if not self.on_history:return 0
        rows=_rows(h); parsed=[]
        for x in rows:
            asset=str(x.get('p',x.get('pair',x.get('symbol',default_asset))) or default_asset).upper()
            # Some API responses use compact o/h/l/c/t fields; others use full names.
            o=_num(x.get('open',x.get('o')));hi=_num(x.get('high',x.get('h')));lo=_num(x.get('low',x.get('l')));c=_num(x.get('close',x.get('c')));t=_num(x.get('t',x.get('timestamp',x.get('time'))))
            if None in (o,hi,lo,c,t) or not asset:continue
            # Normalize milliseconds to seconds.
            if t>10_000_000_000:t/=1000.0
            if hi<max(o,c) or lo>min(o,c):continue
            parsed.append((asset,t,{'open':o,'high':hi,'low':lo,'close':c,'timestamp':t}))
        parsed.sort(key=lambda z:z[1])
        seen=set();count=0
        for asset,t,candle in parsed:
            key=(asset,int(t))
            if key in seen:continue
            seen.add(key);self.on_history(asset,candle);count+=1
        return count
    async def _ticks(self,m):
        rows=m.get('d',[]) if isinstance(m,dict) else m
        if isinstance(rows,dict):rows=[rows]
        if not isinstance(rows,list):return
        for x in rows:
            if not isinstance(x,dict):continue
            asset=str(x.get('p',x.get('pair',x.get('symbol','')))).upper()
            try:price=float(x.get('q',x.get('price',x.get('close'))));ts=float(x.get('t',x.get('timestamp',x.get('time'))))
            except(TypeError,ValueError):continue
            if asset not in self.assets:continue
            if ts>10_000_000_000:ts/=1000.0
            self.tick_count+=1
            if self.tick_count==1:log.info('First live Olymp tick received: asset=%s price=%s',asset,price)
            bucket=int(ts//60)*60;cur=self.forming.get(asset)
            if cur is None or cur['timestamp']!=bucket:
                if cur is not None:self.on_candle(asset,cur)
                cur={'open':price,'high':price,'low':price,'close':price,'timestamp':float(bucket)};self.forming[asset]=cur
            else:cur['high']=max(cur['high'],price);cur['low']=min(cur['low'],price);cur['close']=price
    def status(self):return {'configured':self.enabled,'connected':self.connected,'assets':self.assets,'mode':'READ_ONLY_MARKET_DATA','timeframe':'1m_from_live_ticks','auto_trade':False,'ticks_received':self.tick_count}

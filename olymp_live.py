from __future__ import annotations
import asyncio, logging, os, subprocess, sys, threading
from pathlib import Path
from typing import Callable
log=logging.getLogger('candice.olymp_live')
UPSTREAM='https://github.com/ChipaDevTeam/OlympTradeAPI.git'; LOCAL_API=Path('/tmp/candice_olymptrade_api')

def _load():
    target=LOCAL_API/'olymptrade_ws'
    if not target.exists():subprocess.run(['git','clone','--depth','1',UPSTREAM,str(LOCAL_API)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=60)
    sys.path.insert(0,str(LOCAL_API))
    from olymptrade_ws.core.client import OlympTradeClient
    from olymptrade_ws.olympconfig import parameters
    return OlympTradeClient,parameters

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
                    history=await self.client.market.get_candles(asset,size=60,count=100);await self._history(history or []);log.info('Loaded Olymp 1m candle history: %s',asset)
                except Exception:log.exception('Failed to load Olymp candle history: %s',asset)
                try:
                    await self.client.market.subscribe_ticks(asset);log.info('Subscribed to Olymp live ticks: %s',asset)
                except Exception:log.exception('Failed to subscribe to Olymp live ticks: %s',asset)
            while self.client.connection.is_connected:await asyncio.sleep(15)
        except Exception:log.exception('Olymp live market connection error')
        finally:
            self.connected=False
            try:await self.client.stop()
            except Exception:pass
    async def _history(self,h):
        groups=h.get('d',[]) if isinstance(h,dict) else h
        if isinstance(groups,dict):groups=[groups]
        if not isinstance(groups,list):return
        for g in groups:
            if not isinstance(g,dict):continue
            asset=str(g.get('p',g.get('pair',g.get('symbol','')))).upper();rows=g.get('candles') if isinstance(g.get('candles'),list) else [g]
            for x in rows:
                if not isinstance(x,dict):continue
                try:o=float(x.get('open',x.get('o')));hi=float(x.get('high',x.get('h')));lo=float(x.get('low',x.get('l')));c=float(x.get('close',x.get('c')));t=float(x.get('t',x.get('timestamp',x.get('time'))))
                except(TypeError,ValueError):continue
                if asset and hi>=max(o,c) and lo<=min(o,c) and self.on_history:self.on_history(asset,{'open':o,'high':hi,'low':lo,'close':c,'timestamp':t})
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
            self.tick_count+=1
            if self.tick_count==1:log.info('First live Olymp tick received: asset=%s price=%s',asset,price)
            bucket=int(ts//60)*60;cur=self.forming.get(asset)
            if cur is None or cur['timestamp']!=bucket:
                if cur is not None:self.on_candle(asset,cur)
                cur={'open':price,'high':price,'low':price,'close':price,'timestamp':float(bucket)};self.forming[asset]=cur
            else:cur['high']=max(cur['high'],price);cur['low']=min(cur['low'],price);cur['close']=price
    def status(self):return {'configured':self.enabled,'connected':self.connected,'assets':self.assets,'mode':'READ_ONLY_MARKET_DATA','timeframe':'1m_from_live_ticks','auto_trade':False,'ticks_received':self.tick_count}

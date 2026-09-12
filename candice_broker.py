from __future__ import annotations
import asyncio, logging, time
from olymptrade_ws import OlympTradeClient

log=logging.getLogger('candice.broker')
FLEX_ASSETS=('EURUSD_OTC','GBPUSD_OTC','USDJPY_OTC','USDCHF_OTC','USDCAD_OTC','AUDUSD_OTC','NZDUSD_OTC','EURJPY_OTC','GBPJPY_OTC','AUDJPY_OTC','CADJPY_OTC','XAUUSD_OTC','XAGUSD_OTC','BTCUSD_OTC','ASIA_X')

class Broker:
    def __init__(self,token): self.token=token; self.client=None; self.catalog=set(); self.lock=asyncio.Lock()
    def connected(self): return bool(self.client and self.client.connection.is_connected)
    async def _instruments(self,m):
        d=m.get('d') if isinstance(m,dict) else None
        if isinstance(d,dict): d=d.get('instruments',d.get('pairs',d.get('items',d.get('assets',[]))))
        if isinstance(d,list):
            for x in d:
                if isinstance(x,dict):
                    p=str(x.get('pair') or x.get('symbol') or x.get('name') or '').upper()
                    if p in FLEX_ASSETS or p.endswith('_OTC'): self.catalog.add(p)
    async def connect_forever(self):
        if not self.token: raise RuntimeError('OLYMPTRADE_ACCESS_TOKEN is missing')
        while True:
            try:
                c=OlympTradeClient(access_token=self.token,log_raw_messages=False); c.register_callback(1054,self._instruments); await c.start(); self.client=c; log.warning('CANDICE BROKER: LIVE CONNECTED (FLEX market data only)')
                while c.connection.is_connected: await asyncio.sleep(5)
            except asyncio.CancelledError: raise
            except Exception as e:
                self.client=None; log.warning('CANDICE BROKER: reconnect after %s',e); await asyncio.sleep(5)
    async def candles(self,pair,size=60,count=120,max_age=360):
        c=self.client
        if c is None or not c.connection.is_connected:return None,'broker disconnected'
        try: raw=await c.market.get_candles(pair,size,count)
        except Exception as e:return None,f'candle request failed: {e}'
        if not isinstance(raw,list) or len(raw)<40:return None,'insufficient live candles'
        rows=[]
        for x in raw:
            try:
                ts=float(x.get('timestamp',x.get('t',x.get('time')))); ts=ts/1000 if ts>1e11 else ts
                rows.append((ts,float(x.get('open',x.get('o'))),float(x.get('high',x.get('h'))),float(x.get('low',x.get('l'))),float(x.get('close',x.get('c')))))
            except Exception:pass
        if len(rows)<40:return None,'invalid candle data'
        rows.sort(); age=time.time()-rows[-1][0]
        if age>max_age:return None,f'stale candle age={age:.1f}s'
        import pandas as pd
        return pd.DataFrame(rows,columns=['timestamp','open','high','low','close']),None
    async def live_assets(self):
        candidates=sorted(self.catalog or set(FLEX_ASSETS))
        sem=asyncio.Semaphore(6)
        async def probe(p):
            async with sem:
                df,e=await self.candles(p,60,60,360); return p if e is None and df is not None else None
        found=await asyncio.gather(*(probe(p) for p in candidates),return_exceptions=False)
        return [p for p in found if p]

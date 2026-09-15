from __future__ import annotations
import asyncio, logging, time
import pandas as pd
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

    async def _account_probe(self,c):
        """Read-only account metadata probe; never selects or trades an account.

        The websocket client exposes account-info request 1068 separately from
        the balance push (event 55). We retain both responses so telemetry can
        use an explicit selected/current marker if the broker sends one. We do
        not guess from balance size or from list ordering.
        """
        try:
            metadata={}
            for group in ('demo','real'):
                try:
                    resp=await c.send_request(1068,[{'group':group}],requires_response=True,timeout=8)
                    metadata[group]=resp if isinstance(resp,dict) else {}
                except Exception as e:
                    metadata[group]={'error':type(e).__name__}
            setattr(c,'candice_account_metadata',metadata)
            log.info('CANDICE ACCOUNT METADATA PROBE groups=%s',','.join(sorted(metadata.keys())))
            for group,resp in metadata.items():
                d=resp.get('d') if isinstance(resp,dict) else None
                if isinstance(d,list):
                    safe=[]
                    for row in d:
                        if isinstance(row,dict):
                            safe.append({k:row.get(k) for k in row.keys() if str(k).lower() in {
                                'account_id','accountid','id','group','mode','type','selected','is_selected','active','is_active','current','is_current','status','currency'
                            }})
                    log.info('CANDICE ACCOUNT METADATA group=%s rows=%s',group,safe)
        except Exception:
            log.exception('CANDICE ACCOUNT METADATA PROBE failed')

    async def connect_forever(self):
        if not self.token: raise RuntimeError('OLYMPIATRADE_ACCESS_TOKEN is missing')
        backoff=5
        while True:
            try:
                c=OlympTradeClient(access_token=self.token,log_raw_messages=False)
                c.register_callback(1054,self._instruments)
                await c.start()
                self.client=c
                asyncio.create_task(self._account_probe(c),name='candice-account-probe')
                backoff=5
                log.warning('CANDICE BROKER: LIVE CONNECTED (FLEX market data only)')
                while c.connection.is_connected: await asyncio.sleep(5)
            except asyncio.CancelledError: raise
            except Exception as e:
                self.client=None; log.warning('CANDICE BROKER: reconnect after %s',e); await asyncio.sleep(backoff); backoff=min(backoff*2,60)

    async def candles(self,pair,size=60,count=120,max_age=360):
        c=self.client
        if c is None or not c.connection.is_connected:return None,'broker disconnected'
        try: size=max(1,int(size)); count=max(1,int(count))
        except (TypeError,ValueError): size,count=60,120
        rows=[]; cursor=int(time.time()); chunk_size=min(600,count); max_chunks=max(8,(count//40)+2)
        try:
            for _ in range(max_chunks):
                need=count-len(rows)
                if need<=0: break
                batch_count=min(chunk_size,need)
                raw=await c.market.get_candles(pair,size,batch_count,end_time=cursor)
                if not isinstance(raw,list) or not raw: break
                batch_times=[]
                for x in raw:
                    try:
                        ts=float(x.get('timestamp',x.get('t',x.get('time')))); ts=ts/1000 if ts>1e11 else ts
                        o=float(x.get('open',x.get('o'))); h=float(x.get('high',x.get('h'))); l=float(x.get('low',x.get('l'))); cl=float(x.get('close',x.get('c')))
                        rows.append((ts,o,h,l,cl)); batch_times.append(ts)
                    except Exception: pass
                if not batch_times: break
                oldest=min(batch_times); next_cursor=int(oldest-size)-1
                if next_cursor>=cursor: break
                cursor=next_cursor
        except Exception as e:
            return None,f'candle request failed: {e}'
        rows=sorted(set(rows),key=lambda x:x[0])
        if len(rows)<40:return None,'insufficient live candles'
        age=time.time()-rows[-1][0]
        if age>max_age:return None,f'stale candle age={age:.1f}s'
        if len(rows)<count: log.info('CANDLE HISTORY pair=%s requested=%d received=%d',pair,count,len(rows))
        return pd.DataFrame(rows,columns=['timestamp','open','high','low','close']),None

    @staticmethod
    def closed_candle(df, boundary_ts=None, interval=60):
        if df is None or df.empty:return None
        d=df.copy().sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
        if boundary_ts is None: boundary_ts=time.time()
        cutoff=float(boundary_ts)-float(interval); eligible=d[d['timestamp']<=cutoff]
        if eligible.empty:return None
        return eligible.iloc[-1]

    async def closed_candle_at(self,pair,boundary_ts,interval=60,max_age=180):
        df,err=await self.candles(pair,interval,120,max_age)
        if err or df is None:return None,err or 'no candle data'
        target=float(boundary_ts)-float(interval); d=df.copy().sort_values('timestamp').drop_duplicates('timestamp')
        matches=d[(d['timestamp']>=target-0.5)&(d['timestamp']<=target+0.5)]
        if matches.empty:return None,'exact expiry candle not available yet'
        return matches.iloc[-1],None

    async def live_assets(self):
        candidates=sorted(self.catalog or set(FLEX_ASSETS)); sem=asyncio.Semaphore(6)
        async def probe(p):
            async with sem:
                df,e=await self.candles(p,60,120,360); return p if e is None and df is not None else None
        found=await asyncio.gather(*(probe(p) for p in candidates),return_exceptions=False)
        return [p for p in found if p]

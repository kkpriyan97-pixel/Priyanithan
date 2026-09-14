from __future__ import annotations

from collections import defaultdict
from math import isfinite
import os, sqlite3, json, time

STATE = defaultdict(lambda: {"last_setup_key": None, "last_signal_window": None, "last_direction": None})
MIN_REAL_CANDLES = int(os.getenv("CANDICE_MIN_REAL_CANDLES", "30"))
DB_PATH = os.getenv("CANDICE_OUTCOME_DB", "/tmp/candice_outcomes.sqlite3")
LIVE_CANDLE_MAX_AGE = float(os.getenv("CANDICE_LIVE_CANDLE_MAX_AGE", "90"))

def _f(x):
    try:
        x=float(x); return x if isfinite(x) else None
    except Exception: return None

def _ema(v,p):
    if len(v)<p:return None
    k=2/(p+1); z=sum(v[:p])/p
    for x in v[p:]: z=x*k+z*(1-k)
    return z

def _atr(d,p=14):
    if len(d)<=p:return None
    return sum(max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close'])) for a,b in zip(d[-p-1:-1],d[-p:]))/p

def _rsi(v,p=14):
    if len(v)<=p:return None
    g=l=0
    for a,b in zip(v[-p-1:-1],v[-p:]):
        x=b-a; g+=max(x,0); l+=max(-x,0)
    if l==0:return 100.0
    return 100-100/(1+g/l)

def _candle_features(d):
    if len(d)<3:return 0,'none'
    a,b=d[-2],d[-1]; body=abs(b['close']-b['open']); rng=max(b['high']-b['low'],1e-12)
    up=b['high']-max(b['open'],b['close']); lo=min(b['open'],b['close'])-b['low']
    if body/rng<.30 and lo/rng>.55:return 1,'hammer_rejection'
    if body/rng<.30 and up/rng>.55:return -1,'shooting_star_rejection'
    if b['close']>b['open'] and a['close']<a['open'] and b['close']>=a['open'] and b['open']<=a['close']:return 1,'bullish_engulfing'
    if b['close']<b['open'] and a['close']>a['open'] and b['open']>=a['close'] and b['close']<=a['open']:return -1,'bearish_engulfing'
    return (1 if b['close']>b['open'] else -1 if b['close']<b['open'] else 0),'momentum_candle'

def _structure(d):
    c=[x['close'] for x in d]; atr=_atr(d) or max(abs(c[-1])*1e-5,1e-8)
    recent=d[-12:]; highs=[x['high'] for x in recent]; lows=[x['low'] for x in recent]
    hh=sum(1 for a,b in zip(highs[-5:-1],highs[-4:]) if b>a); ll=sum(1 for a,b in zip(lows[-5:-1],lows[-4:]) if b>a)
    up=sum(1 for a,b in zip(c[-6:-1],c[-5:]) if b>a); down=4-up
    e9,e21,e50=_ema(c,9),_ema(c,21),_ema(c,50)
    trend=1 if e9 and e21 and e9>e21 else -1 if e9 and e21 and e9<e21 else 0
    score=0; reasons=[]
    if up>=4: score+=3; reasons.append('recent candles show sustained buying')
    if down>=3: score-=3; reasons.append('recent candles show sustained selling')
    if hh>=2 and ll>=2: score+=2 if trend>=0 else -2; reasons.append('price structure is directional')
    if hh==0 and ll==0: reasons.append('range-like recent structure')
    body=sum(abs(x['close']-x['open']) for x in recent[-5:])/5
    rng=sum(max(x['high']-x['low'],0) for x in recent[-5:])/5
    if rng>0 and body/rng>.58:
        score += 2 if c[-1]>c[-5] else -2
        reasons.append('strong body-to-range momentum')
    return score,trend,atr,e9,e21,e50,reasons

def _learn(asset, pattern, regime, direction):
    try:
        con=sqlite3.connect(DB_PATH,timeout=2)
        rows=con.execute('SELECT result FROM signals WHERE asset=? AND result IS NOT NULL AND strategy=? AND regime=? ORDER BY id DESC LIMIT 40',(asset,str(pattern),str(regime))).fetchall()
        con.close()
        if not rows:return 0.0,0,[]
        wins=sum(r[0]=='WIN' for r in rows); losses=sum(r[0]=='LOSS' for r in rows); n=wins+losses
        if n<4:return 0.0,n,[]
        wr=wins/n; adj=max(-2.0,min(2.0,(wr-.5)*6))
        return adj,n,[f'learned pattern history {wins}W/{losses}L']
    except Exception:return 0.0,0,[]

def evaluate(asset,data,base):
    if len(data)<MIN_REAL_CANDLES:
        return {'allow':False,'regime':'WARMUP','strategy':'market_brain','score':0,'reasons':[f'real candle warmup {len(data)}/{MIN_REAL_CANDLES}']}
    try:
        latest=float(data[-1].get('timestamp',0) or 0)
        if latest>10000000000: latest/=1000
        age=time.time()-latest
        if age>LIVE_CANDLE_MAX_AGE:
            return {'allow':False,'regime':'MARKET_CLOSED','strategy':'market_brain','score':0,'reasons':[f'no fresh live candle ({int(age)}s old)','market session not currently active']}
    except Exception:
        return {'allow':False,'regime':'MARKET_CLOSED','strategy':'market_brain','score':0,'reasons':['invalid live candle timestamp']}
    c=[x['close'] for x in data]; last=c[-1]
    ms,trend,atr,e9,e21,e50,reasons=_structure(data)
    candle,pattern=_candle_features(data)
    direction=1 if ms>0 and candle>=0 else -1 if ms<0 and candle<=0 else 0
    if pattern in ('hammer_rejection','bullish_engulfing') and ms>=0: direction=1
    if pattern in ('shooting_star_rejection','bearish_engulfing') and ms<=0: direction=-1
    if direction==0:
        return {'allow':False,'regime':'TRANSITION','strategy':'market_brain','score':0,'reasons':reasons+['market/candle structure is not decisive']}
    regime='TREND_UP' if ms>=5 else 'TREND_DOWN' if ms<=-5 else 'REVERSAL' if pattern.endswith('rejection') or 'engulfing' in pattern else 'TRANSITION'
    confirmations=0
    if e9 and e21 and ((e9>e21)==(direction>0)): confirmations+=1
    r=_rsi(c)
    if r is not None and ((r>=45)==(direction>0)) and not (r>78 or r<22): confirmations+=1
    mtf=base.get('mtf') or {}
    if float(mtf.get('1m',0) or 0)*direction>0: confirmations+=1
    learn_adj,n,learn_notes=_learn(asset,pattern,regime,'UP' if direction>0 else 'DOWN')
    quality=58+min(24,abs(ms)*4)+min(8,confirmations*3)+learn_adj
    if pattern!='momentum_candle': quality+=5
    quality=int(max(0,min(95,quality)))
    setup_key=f'{regime}:{pattern}:{direction}:{round(last/max(atr,1e-12),1)}'
    now=time.time(); window=int(now//300)
    st=STATE[asset]
    if st['last_setup_key']==setup_key and st['last_signal_window']==window:
        return {'allow':False,'regime':regime,'strategy':'market_brain','score':quality,'reasons':['same setup already considered in this 5-minute window']}
    if abs(ms)<3:
        return {'allow':False,'regime':regime,'strategy':'market_brain','score':quality,'reasons':reasons+['waiting: market structure not strong enough']}
    if quality<68:
        return {'allow':False,'regime':regime,'strategy':'market_brain','score':quality,'reasons':reasons+['waiting: setup quality below threshold']}
    st.update(last_setup_key=setup_key,last_signal_window=window,last_direction=direction)
    return {'allow':True,'regime':regime,'strategy':'market_brain','score':quality,'direction':'UP' if direction>0 else 'DOWN','pattern':pattern,'reasons':reasons+learn_notes+[f'candle/market brain score={ms}',f'indicator confirmations={confirmations} (confirmation only)',f'brain quality={quality}%']}

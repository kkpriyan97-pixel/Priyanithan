"""Candice own-strategy technical brain. Read-only; never places orders."""
from __future__ import annotations
from math import isfinite

EXPIRIES=(1,2,3,4,5,10,15)

def _f(x,d=0.0):
    try:
        v=float(x); return v if isfinite(v) else d
    except Exception:return d

def _norm(c):
    return {"time":c.get("time",c.get("t")),"open":_f(c.get("open",c.get("o"))),
            "high":_f(c.get("high",c.get("h"))),"low":_f(c.get("low",c.get("l"))),
            "close":_f(c.get("close",c.get("c"))),"volume":_f(c.get("volume",c.get("v")))}

def ema(v,n):
    if not v:return 0.0
    k=2/(n+1);e=v[0]
    for x in v[1:]:e=x*k+e*(1-k)
    return e

def rsi(v,n=14):
    if len(v)<n+1:return 50.0
    g=[];l=[]
    for a,b in zip(v[-n-1:-1],v[-n:]):
        d=b-a;g.append(max(d,0));l.append(max(-d,0))
    ag=sum(g)/n;al=sum(l)/n
    return 100.0 if al==0 else 100-(100/(1+ag/al))

def atr(cs,n=14):
    if len(cs)<2:return 0.0
    t=[]
    for a,b in zip(cs[-n-1:-1],cs[-n:]):
        t.append(max(b["high"]-b["low"],abs(b["high"]-a["close"]),abs(b["low"]-a["close"])))
    return sum(t)/len(t) if t else 0.0

def _directional_15m(cs):
    """Aggregate 1m candles into closed 15m blocks; never call 1m data '15m'."""
    usable=cs[:-1] if len(cs)>1 else cs
    blocks=[]
    for i in range(0,len(usable)-14,15):
        b=usable[i:i+15]
        if len(b)==15:blocks.append(b)
    if len(blocks)<2:return "SIDEWAYS"
    a,b=blocks[-2],blocks[-1]
    ac,bc=a[-1]["close"],b[-1]["close"]
    ah,al=max(x["high"] for x in a),min(x["low"] for x in a)
    bh,bl=max(x["high"] for x in b),min(x["low"] for x in b)
    if bc>ac and bh>=ah:return "UP"
    if bc<ac and bl<=al:return "DOWN"
    return "SIDEWAYS"

def analyze_asset(asset,candles,price=None):
    cs=[_norm(c) for c in candles if isinstance(c,dict)]
    if len(cs)<45:return None

    # Use only completed 1m candles for the decision. Current tick is entry price only.
    usable=cs[:-1] if len(cs)>1 else cs
    v=[c["close"] for c in usable]
    last=usable[-1];p=_f(price,last["close"])
    e9=ema(v[-30:],9);e21=ema(v[-30:],21);rr=rsi(v);aa=atr(usable)
    hi=max(c["high"] for c in usable[-30:]);lo=min(c["low"] for c in usable[-30:])
    body=last["close"]-last["open"];rng=max(last["high"]-last["low"],1e-12)
    upper=last["high"]-max(last["open"],last["close"])
    lower=min(last["open"],last["close"])-last["low"]
    trend=_directional_15m(cs)

    structure="BULLISH" if e9>e21 and rr>=52 and p>=e9 else "BEARISH" if e9<e21 and rr<=48 and p<=e9 else "MIXED"
    pattern=("BULLISH_CANDLE" if body>0 and body/rng>=.55 else
             "BEARISH_CANDLE" if body<0 and -body/rng>=.55 else
             "PIN_REJECTION" if max(upper,lower)/rng>.45 else "NEUTRAL")

    direction=None;strategy=""
    if trend=="UP" and structure=="BULLISH" and (pattern=="BULLISH_CANDLE" or p>e9):
        direction,strategy="UP","TREND_FOLLOWING"
    elif trend=="DOWN" and structure=="BEARISH" and (pattern=="BEARISH_CANDLE" or p<e9):
        direction,strategy="DOWN","TREND_FOLLOWING"
    elif trend=="UP" and lower>max(abs(body)*1.2,rng*.25) and p>=e21:
        direction,strategy="UP","PULLBACK"
    elif trend=="DOWN" and upper>max(abs(body)*1.2,rng*.25) and p<=e21:
        direction,strategy="DOWN","PULLBACK"
    elif trend=="UP" and p>=hi-aa*.25 and body>0:
        direction,strategy="UP","BREAKOUT"
    elif trend=="DOWN" and p<=lo+aa*.25 and body<0:
        direction,strategy="DOWN","BREAKOUT"

    if not direction or trend=="SIDEWAYS" or structure=="MIXED":return None

    # Own strategy score: independent evidence must agree; confidence is not
    # artificially inflated from one indicator.
    trend_score=20
    momentum_score=20 if (direction=="UP" and rr>=55) or (direction=="DOWN" and rr<=45) else 10
    structure_score=20
    candle_score=20 if ((direction=="UP" and body>0) or (direction=="DOWN" and body<0)) else 8
    distance_score=min(20,max(0,int(abs(p-e21)/(aa or 1)*8)))
    score=trend_score+momentum_score+structure_score+candle_score+distance_score
    confidence=min(99,score)
    if confidence<90:return None

    expiry=5
    if strategy=="BREAKOUT":expiry=1
    elif strategy=="PULLBACK":expiry=2
    elif abs(rr-50)>=15:expiry=3

    return {
        "pair":str(asset.get("pair","")),"display_name":str(asset.get("display_name") or asset.get("title") or ""),
        "direction":direction,"confidence":confidence,"strategy":strategy,"expiry_minutes":expiry,
        "pattern":pattern,"trend_15m":trend,"structure_1m":structure,"market_quality":round(score,2),
        "reason":f"{strategy}: {pattern}; 15m={trend}; 1m={structure}; RSI={rr:.1f}; EMA9/21 aligned; independent-score={score}.",
        "entry_candle_ts":last["time"],"price":p
    }

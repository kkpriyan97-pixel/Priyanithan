"""Candice live technical brain.

ONLY these two technical components generate live direction:
1) Anchored VWAP
2) Volume Profile (POC / VAH / VAL)

No EMA, RSI, MACD, ATR, stochastic, Supertrend, or standalone candle-pattern
strategy is used by the live brain. All decisions use fully closed 1-minute
candles. The 3-minute scheduler is owned by app.py and is unchanged.
"""
from __future__ import annotations

import time
from math import isfinite

EXPIRIES=(1,)
MIN_CLOSED_CANDLES=60
ONE_MINUTE=60
FIFTEEN_MINUTES=900
CLOSE_GRACE_SECONDS=1
PROFILE_LOOKBACK=60
PROFILE_BINS=24
VALUE_AREA_FRACTION=0.70


def _f(x,d=0.0):
    try:
        v=float(x)
        return v if isfinite(v) else d
    except Exception:
        return d


def _ts(x,d=0.0):
    try:
        v=float(x)
        if v>20_000_000_000:
            v/=1000.0
        return v if isfinite(v) else d
    except Exception:
        return d


def _norm(c):
    return {
        "time":c.get("time",c.get("t")),
        "open":_f(c.get("open",c.get("o"))),
        "high":_f(c.get("high",c.get("h"))),
        "low":_f(c.get("low",c.get("l"))),
        "close":_f(c.get("close",c.get("c"))),
        "volume":_f(c.get("volume",c.get("v")),1.0),
    }


def _prepare_closed_1m(candles,now=None):
    """Normalize, deduplicate and keep only fully closed 1-minute candles."""
    now=time.time() if now is None else float(now)
    by_minute={}
    for raw in candles or []:
        if not isinstance(raw,dict):
            continue
        c=_norm(raw)
        ts=_ts(c["time"],-1)
        if ts<0:
            continue
        minute=int(ts//ONE_MINUTE)*ONE_MINUTE
        c["time"]=minute
        if minute+ONE_MINUTE>now-CLOSE_GRACE_SECONDS:
            continue
        by_minute[minute]=c
    return [by_minute[k] for k in sorted(by_minute)]


def _aggregate_closed_15m(closed_1m,now=None):
    """Build complete 15-minute blocks for the AVWAP anchor only."""
    now=time.time() if now is None else float(now)
    groups={}
    for c in closed_1m:
        ts=int(c["time"])
        bucket=(ts//FIFTEEN_MINUTES)*FIFTEEN_MINUTES
        if bucket+FIFTEEN_MINUTES>now-CLOSE_GRACE_SECONDS:
            continue
        groups.setdefault(bucket,[]).append(c)

    out=[]
    for bucket in sorted(groups):
        bars=sorted(groups[bucket],key=lambda x:x["time"])
        expected=[bucket+i*ONE_MINUTE for i in range(15)]
        if len(bars)!=15 or [int(x["time"]) for x in bars]!=expected:
            continue
        out.append({
            "time":bucket,
            "open":bars[0]["open"],
            "high":max(x["high"] for x in bars),
            "low":min(x["low"] for x in bars),
            "close":bars[-1]["close"],
            "volume":sum(max(x.get("volume",0.0),1.0) for x in bars),
        })
    return out


def _anchored_vwap(cs,anchor_ts):
    subset=[c for c in cs if int(c["time"])>=int(anchor_ts)]
    if not subset:
        return 0.0,0.0
    pv=0.0
    vv=0.0
    for c in subset:
        typical=(c["high"]+c["low"]+c["close"])/3.0
        vol=max(_f(c.get("volume"),1.0),1.0)
        pv+=typical*vol
        vv+=vol
    return (pv/vv if vv else 0.0),vv


def _volume_profile(cs,bins=PROFILE_BINS):
    """Approximate 1m volume-at-price using candle typical price and volume."""
    sample=list(cs[-PROFILE_LOOKBACK:])
    if not sample:
        return None

    lo=min(c["low"] for c in sample)
    hi=max(c["high"] for c in sample)
    total=sum(max(_f(c.get("volume"),1.0),1.0) for c in sample)

    if hi<=lo:
        p=sample[-1]["close"]
        return {
            "poc":p,"vah":p,"val":p,
            "range_high":hi,"range_low":lo,
            "total_volume":total,"bins":1,
        }

    step=(hi-lo)/bins
    volumes=[0.0]*bins
    for c in sample:
        typical=(c["high"]+c["low"]+c["close"])/3.0
        idx=int((typical-lo)/step)
        idx=max(0,min(bins-1,idx))
        volumes[idx]+=max(_f(c.get("volume"),1.0),1.0)

    poc_idx=max(range(bins),key=lambda i:volumes[i])
    target=sum(volumes)*VALUE_AREA_FRACTION
    accumulated=volumes[poc_idx]
    left=right=poc_idx

    while accumulated<target and (left>0 or right<bins-1):
        left_vol=volumes[left-1] if left>0 else -1.0
        right_vol=volumes[right+1] if right<bins-1 else -1.0
        if right_vol>=left_vol:
            right+=1
            accumulated+=volumes[right]
        else:
            left-=1
            accumulated+=volumes[left]

    return {
        "poc":lo+(poc_idx+0.5)*step,
        "vah":min(hi,lo+(right+1)*step),
        "val":max(lo,lo+left*step),
        "range_high":hi,
        "range_low":lo,
        "total_volume":sum(volumes),
        "bins":bins,
    }


def _avwap_slope(cs,anchor_ts):
    if len(cs)<2:
        return 0.0
    current,_=_anchored_vwap(cs,anchor_ts)
    previous,_=_anchored_vwap(cs[:-1],anchor_ts)
    return current-previous


def analyze_asset(asset,candles,price=None,now=None):
    """Generate a signal only when the two indicators strongly agree."""
    now=time.time() if now is None else float(now)
    cs=_prepare_closed_1m(candles,now)
    if len(cs)<MIN_CLOSED_CANDLES:
        return None

    blocks_15m=_aggregate_closed_15m(cs,now)
    if not blocks_15m:
        return None

    last=cs[-1]
    p=_f(price,last["close"])
    anchor_ts=int(blocks_15m[-1]["time"])

    avwap,avwap_volume=_anchored_vwap(cs,anchor_ts)
    previous_avwap,_=_anchored_vwap(cs[:-1],anchor_ts)
    profile=_volume_profile(cs)
    if avwap<=0 or not profile:
        return None

    poc=profile["poc"]
    vah=profile["vah"]
    val=profile["val"]
    slope=avwap-previous_avwap

    # Strong acceptance: price must be on the same side of AVWAP and POC,
    # and outside the corresponding Volume Profile value area, with AVWAP
    # moving in the same direction. No other indicator is consulted.
    up=(
        p>avwap
        and p>poc
        and p>=vah
        and slope>0
    )
    down=(
        p<avwap
        and p<poc
        and p<=val
        and slope<0
    )

    if not (up or down):
        return None

    direction="UP" if up else "DOWN"

    # Transparent confluence score from the two-indicator conditions.
    # This is a rule score, not a statistical win probability.
    confluence_score=0
    confluence_score+=30 if (p>avwap if up else p<avwap) else 0
    confluence_score+=30 if (p>poc if up else p<poc) else 0
    confluence_score+=25 if (p>=vah if up else p<=val) else 0
    confluence_score+=15 if (slope>0 if up else slope<0) else 0

    return {
        "pair":str(asset.get("pair","")),
        "display_name":str(asset.get("display_name") or asset.get("title") or ""),
        "direction":direction,
        "confidence":min(99,int(round(90+confluence_score*0.09))),
        "strategy":"AVWAP_VOLUME_PROFILE",
        "expiry_minutes":1,
        "pattern":"AVWAP_VALUE_ACCEPTANCE",
        "trend_15m":"AVWAP_UP" if up else "AVWAP_DOWN",
        "structure_1m":"ABOVE_AVWAP_POC_VAH" if up else "BELOW_AVWAP_POC_VAL",
        "market_quality":round(confluence_score,2),
        "confluence_score":confluence_score,
        "reason":(
            f"AVWAP={avwap:.8f}; POC={poc:.8f}; VAH={vah:.8f}; VAL={val:.8f}; "
            f"AVWAP_slope={slope:.8f}; two-indicator confluence; closed-1m only."
        ),
        "indicator_features":{
            "anchored_vwap":avwap,
            "volume_profile_poc":poc,
            "volume_profile_vah":vah,
            "volume_profile_val":val,
            "avwap_slope":slope,
            "profile_range_high":profile["range_high"],
            "profile_range_low":profile["range_low"],
            "profile_total_volume":profile["total_volume"],
            "profile_bins":profile["bins"],
            "anchor_15m_start_ts":anchor_ts,
            "avwap_volume":avwap_volume,
        },
        "avwap":avwap,
        "poc":poc,
        "vah":vah,
        "val":val,
        "avwap_slope":slope,
        "entry_candle_ts":last["time"],
        "price":p,
        "decision_candle_closed":True,
        "closed_1m_ts":last["time"],
        "closed_15m_ts":blocks_15m[-1]["time"],
    }

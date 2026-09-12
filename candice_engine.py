from __future__ import annotations
import asyncio, json, logging, math, os, time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import requests

log = logging.getLogger("candice.engine")
EXPIRIES = (2, 3, 5, 10, 15)
MIN_CONF = int(os.getenv("AI_MIN_CONFIDENCE", "72"))
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT_SECONDS", "18"))

@dataclass
class Analysis:
    asset: str
    decision: str
    direction: str = ""
    confidence: int = 0
    expiry: int = 0
    score: float = 0.0
    reason: str = ""
    timeframe: str = "5m"


def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _rsi(s, n=14):
    d=s.diff(); up=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean(); dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean(); rs=up/dn.replace(0,1e-12); return 100-(100/(1+rs))
def _atr(df,n=14):
    pc=df.close.shift(1); tr=pd.concat([(df.high-df.low),(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1); return tr.rolling(n).mean()
def _macd(s): return _ema(s,12)-_ema(s,26)
def _adx(df,n=14):
    up=df.high.diff(); dn=-df.low.diff(); plus=up.where((up>dn)&(up>0),0.0); minus=dn.where((dn>up)&(dn>0),0.0); atr=_atr(df,n).replace(0,1e-12); p=100*plus.ewm(alpha=1/n,adjust=False).mean()/atr; m=100*minus.ewm(alpha=1/n,adjust=False).mean()/atr; dx=(100*(p-m).abs()/(p+m).replace(0,1e-12)); return dx.ewm(alpha=1/n,adjust=False).mean()


def technical_snapshot(df: pd.DataFrame) -> dict:
    c=df.close
    e9,e21,e50=_ema(c,9),_ema(c,21),_ema(c,50)
    r=_rsi(c); m=_macd(c); sig=_ema(m,9); a=_adx(df); atr=_atr(df)
    hh=c.rolling(20).max(); ll=c.rolling(20).min(); mid=(hh+ll)/2
    last=df.iloc[-1]; prev=df.iloc[-2]
    votes=[]
    votes.append("UP" if e9.iloc[-1]>e21.iloc[-1] else "DOWN")
    votes.append("UP" if c.iloc[-1]>e50.iloc[-1] else "DOWN")
    votes.append("UP" if m.iloc[-1]>sig.iloc[-1] else "DOWN")
    votes.append("UP" if r.iloc[-1]>52 else "DOWN")
    votes.append("UP" if c.iloc[-1]>mid.iloc[-1] else "DOWN")
    up=votes.count("UP"); down=votes.count("DOWN")
    direction="UP" if up>down else "DOWN" if down>up else ""
    strength=max(up,down)/len(votes)
    body=abs(last.close-last.open); rng=max(last.high-last.low,1e-12)
    return {"price":float(last.close),"direction":direction,"vote_up":up,"vote_down":down,"strength":strength,
            "ema9":float(e9.iloc[-1]),"ema21":float(e21.iloc[-1]),"ema50":float(e50.iloc[-1]),
            "rsi14":float(r.iloc[-1]),"macd":float(m.iloc[-1]),"macd_signal":float(sig.iloc[-1]),
            "adx14":float(a.iloc[-1]),"atr14":float(atr.iloc[-1]),"body_ratio":float(body/rng),
            "range20":float(hh.iloc[-1]-ll.iloc[-1]),"prev_close":float(prev.close)}


def choose_expiry(s: dict) -> int:
    if s["adx14"] >= 30 and s["strength"] >= .8: return 5
    if s["adx14"] >= 22 and s["strength"] >= .8: return 3
    if s["body_ratio"] >= .65 and s["strength"] >= .8: return 2
    if s["strength"] >= .6: return 5
    return 0


def _ai_prompt(s, memory):
    return f'''You are Candice, a conservative professional fixed-time/FLEX market analyst. Manual alerts only; never place trades. Analyze evidence, do not predict certainty. Approve only when technical structure is aligned and fresh. Return ONLY JSON: {{"decision":"APPROVE|REJECT","direction":"UP|DOWN|","confidence":0-100,"expiry":2|3|5|10|15,"reason":"short evidence-based reason"}}. Data: {json.dumps(s)}. Historical memory: {json.dumps(memory)[:5000]}'''


def ai_review(snapshot, memory):
    key=os.getenv("OPENROUTER_API_KEY","").strip()
    if not key: return {"decision":"REJECT","confidence":0,"reason":"AI provider not configured"}
    model=os.getenv("OPENROUTER_MODEL","openrouter/free").strip()
    try:
        r=requests.post("https://openrouter.ai/api/v1/chat/completions",headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},json={"model":model,"messages":[{"role":"system","content":"Return strict JSON only."},{"role":"user","content":_ai_prompt(snapshot,memory)}],"temperature":0.1},timeout=AI_TIMEOUT)
        r.raise_for_status(); content=r.json()["choices"][0]["message"]["content"]
        a=json.loads(content[content.find("{"):content.rfind("}")+1])
        return a if isinstance(a,dict) else {"decision":"REJECT","confidence":0,"reason":"invalid AI response"}
    except Exception as e:
        log.warning("AI review unavailable: %s",e); return {"decision":"REJECT","confidence":0,"reason":"AI review unavailable"}


async def analyze(asset, broker, memory):
    df,err=await broker.candles(asset,60,120,360)
    if err: return Analysis(asset,"REJECT",reason=err)
    snap=technical_snapshot(df)
    expiry=choose_expiry(snap)
    if not expiry: return Analysis(asset,"REJECT",reason="Technical alignment too weak")
    ai=await asyncio.to_thread(ai_review,snap,memory)
    decision=str(ai.get("decision","")).upper(); direction=str(ai.get("direction","")).upper(); conf=int(ai.get("confidence",0) or 0); ai_exp=int(ai.get("expiry",0) or 0)
    if decision!="APPROVE" or direction!=snap["direction"] or conf<MIN_CONF or ai_exp not in EXPIRIES:
        return Analysis(asset,"REJECT",direction=direction,confidence=conf,expiry=ai_exp,score=snap["strength"],reason=str(ai.get("reason","AI gate rejected")),timeframe="5m")
    return Analysis(asset,"APPROVE",direction=direction,confidence=conf,expiry=ai_exp,score=snap["strength"],reason=str(ai.get("reason","Technical and AI alignment")),timeframe="5m")


def session_state(now=None):
    now=now or datetime.now(timezone.utc); h=now.hour%3
    return "SIGNAL" if h<2 else "RESEARCH"

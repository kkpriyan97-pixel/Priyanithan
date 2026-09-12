from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
import pandas as pd
import requests

log=logging.getLogger("candice.engine")
EXPIRIES=(1,2,3,5,10,15)
MIN_CONF=int(os.getenv("AI_MIN_CONFIDENCE","72"))
AI_TIMEOUT=float(os.getenv("AI_TIMEOUT_SECONDS","18"))
ANALYSIS_CANDLE_COUNT=max(240,int(os.getenv("ANALYSIS_CANDLE_COUNT","240")))
MIN_CONTEXT_CANDLES=60

@dataclass
class Analysis:
    asset:str
    decision:str
    direction:str=""
    confidence:int=0
    expiry:int=0
    score:float=0.0
    reason:str=""
    timeframe:str="5m"
    evidence:tuple[str,...]=()

def _ema(s,n): return s.ewm(span=n,adjust=False).mean()
def _rsi(s,n=14):
    d=s.diff();up=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean();dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean();rs=up/dn.replace(0,1e-12);return 100-(100/(1+rs))
def _atr(df,n=14):
    pc=df.close.shift(1);tr=pd.concat([(df.high-df.low),(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1);return tr.rolling(n).mean()
def _macd(s): return _ema(s,12)-_ema(s,26)
def _adx(df,n=14):
    up=df.high.diff();dn=-df.low.diff();plus=up.where((up>dn)&(up>0),0.0);minus=dn.where((dn>up)&(dn>0),0.0);atr=_atr(df,n).replace(0,1e-12);p=100*plus.ewm(alpha=1/n,adjust=False).mean()/atr;m=100*minus.ewm(alpha=1/n,adjust=False).mean()/atr;dx=100*(p-m).abs()/(p+m).replace(0,1e-12);return dx.ewm(alpha=1/n,adjust=False).mean()
def _psar_direction(df):
    c=df.close;e=_ema(c,5);hh=df.high.rolling(5).max();ll=df.low.rolling(5).min();up=(c>e)&(c>ll.shift(1));down=(c<e)&(c<hh.shift(1));return "UP" if bool(up.iloc[-1]) else "DOWN" if bool(down.iloc[-1]) else ""
def _donchian(df,n=20):
    upper=df.high.rolling(n).max().shift(1);lower=df.low.rolling(n).min().shift(1);c=df.close.iloc[-1]
    if pd.notna(upper.iloc[-1]) and c>upper.iloc[-1]:return "UP"
    if pd.notna(lower.iloc[-1]) and c<lower.iloc[-1]:return "DOWN"
    return ""
def _roc_direction(c,n=9):
    if len(c)<=n:return ""
    roc=(c.iloc[-1]/c.iloc[-1-n]-1)*100;return "UP" if roc>0 else "DOWN" if roc<0 else ""
def _safe(v,default=0.0):
    try:return float(v) if pd.notna(v) else default
    except Exception:return default

def closed_1m(df:pd.DataFrame,now_ts:float|None=None)->pd.DataFrame:
    if df is None or df.empty:return df.copy() if df is not None else pd.DataFrame()
    now_ts=time.time() if now_ts is None else float(now_ts);cutoff=(int(now_ts)//60)*60-60;d=df.copy().sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True);return d[d.timestamp<=cutoff].reset_index(drop=True)

def technical_snapshot(df:pd.DataFrame)->dict:
    if len(df)<MIN_CONTEXT_CANDLES:raise ValueError("insufficient candles")
    d=df.copy().sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True);c=d.close;e9,e21,e50=_ema(c,9),_ema(c,21),_ema(c,50);r=_rsi(c);m=_macd(c);sig=_ema(m,9);adx=_adx(d);atr=_atr(d);hh=c.rolling(20).max();ll=c.rolling(20).min();mid=(hh+ll)/2;last,prev=d.iloc[-1],d.iloc[-2]
    votes=["UP" if e9.iloc[-1]>e21.iloc[-1] else "DOWN","UP" if c.iloc[-1]>e50.iloc[-1] else "DOWN","UP" if m.iloc[-1]>sig.iloc[-1] else "DOWN","UP" if r.iloc[-1]>52 else "DOWN","UP" if c.iloc[-1]>mid.iloc[-1] else "DOWN"];up,down=votes.count("UP"),votes.count("DOWN");direction="UP" if up>down else "DOWN" if down>up else "";strength=max(up,down)/len(votes);psar=_psar_direction(d);donchian=_donchian(d);roc=_roc_direction(c);macd_cross="UP" if m.iloc[-1]>sig.iloc[-1] and m.iloc[-2]<=sig.iloc[-2] else "DOWN" if m.iloc[-1]<sig.iloc[-1] and m.iloc[-2]>=sig.iloc[-2] else "";ma_cross="UP" if e9.iloc[-1]>e21.iloc[-1] and e9.iloc[-2]<=e21.iloc[-2] else "DOWN" if e9.iloc[-1]<e21.iloc[-1] and e9.iloc[-2]>=e21.iloc[-2] else "";named={"PSAR":psar,"MA":"UP" if e9.iloc[-1]>e21.iloc[-1] else "DOWN","DONCHIAN":donchian,"MACD":"UP" if m.iloc[-1]>sig.iloc[-1] else "DOWN","ROC":roc};agreement=sum(1 for v in named.values() if v and v==direction);conflicts=sum(1 for v in named.values() if v and v!=direction);body=abs(last.close-last.open);rng=max(last.high-last.low,1e-12)
    return {"price":_safe(last.close),"direction":direction,"vote_up":up,"vote_down":down,"strength":strength,"ema9":_safe(e9.iloc[-1]),"ema21":_safe(e21.iloc[-1]),"ema50":_safe(e50.iloc[-1]),"rsi14":_safe(r.iloc[-1]),"macd":_safe(m.iloc[-1]),"macd_signal":_safe(sig.iloc[-1]),"adx14":_safe(adx.iloc[-1]),"atr14":_safe(atr.iloc[-1]),"body_ratio":_safe(body/rng),"range20":_safe(hh.iloc[-1]-ll.iloc[-1]),"prev_close":_safe(prev.close),"psar_direction":psar,"ma_crossover":ma_cross,"donchian_breakout":donchian,"macd_crossover":macd_cross,"roc_direction":roc,"indicator_agreement":agreement,"indicator_conflicts":conflicts,"candle_ts":int(_safe(last.timestamp))}

def resample_ohlc(df:pd.DataFrame,minutes:int)->pd.DataFrame:
    minutes=max(1,int(minutes));d=df.copy().sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    if d.empty:return d[["timestamp","open","high","low","close"]]
    d["dt"]=pd.to_datetime(d["timestamp"],unit="s",utc=True);d=d.set_index("dt");out=d[["open","high","low","close"]].resample(f"{minutes}min",label="left",closed="left").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna().reset_index();latest_start=int(float(df.sort_values("timestamp").iloc[-1]["timestamp"]));complete_until=latest_start+60;out=out[(out.dt.astype("int64")//10**9+minutes*60)<=complete_until].copy();out["timestamp"]=(out.dt.astype("int64")//10**9).astype(int);return out[["timestamp","open","high","low","close"]].reset_index(drop=True)

def human_brain(frames:dict,memory:dict|None=None):
    s=frames.get("5m") or frames.get("1m") or {};one=frames.get("1m",{});three=frames.get("3m",{});ten=frames.get("10m",{});fifteen=frames.get("15m",{});direction=s.get("direction","");memory=memory or {}
    if not direction:return {"approve":False,"direction":"","score":0.0,"expiry":0,"regime":"unclear","reason":"No dominant direction"}
    score=0.0;reasons=[];aligned=0;conflicts=0
    for f in (one,three,s,ten,fifteen):
        if not f:continue
        if f.get("direction")==direction:aligned+=1;score+=.12
        elif f.get("direction"):conflicts+=1;score-=.10
    strength=float(s.get("strength",0));adx=float(s.get("adx14",0));rsi=float(s.get("rsi14",50));body=float(s.get("body_ratio",0));agreement=int(s.get("indicator_agreement",0));ind_conf=int(s.get("indicator_conflicts",0))
    if strength>=.8 and adx>=25:regime="trend"
    elif agreement>=4 and body>=.55:regime="breakout"
    elif adx<18:regime="range"
    else:regime="transition"
    score+=strength*.35+min(agreement,5)*.04+min(adx/40,1)*.10
    if ind_conf>=3:score-=.18;reasons.append("indicator conflict")
    if rsi>=75 or rsi<=25:score-=.18;reasons.append("overextended RSI")
    if body<.25:score-=.10;reasons.append("weak candle body")
    if conflicts>=3:score-=.18;reasons.append("multi-timeframe conflict")
    if aligned>=4:reasons.append("multi-timeframe alignment")
    if agreement>=4:reasons.append("indicator agreement")
    if adx>=25:reasons.append("trend strength")
    if regime=="range" and agreement<4:score-=.10;reasons.append("range market")
    score=max(0.0,min(1.0,score))
    if score>=.86 and adx>=35 and body>=.55:expiry=1
    elif score>=.82 and adx>=30:expiry=2
    elif score>=.76 and adx>=22:expiry=3
    elif score>=.68 and adx>=18:expiry=5
    elif direction and ten.get("direction")==direction and fifteen.get("direction")==direction and score>=.72:expiry=15
    elif direction and ten.get("direction")==direction and fifteen.get("direction")==direction and score>=.62:expiry=10
    else:expiry=0
    approve=bool(expiry and score>=.68 and conflicts<3 and not (rsi>=82 or rsi<=18))
    reason="; ".join(reasons) or "insufficient independent confirmation"
    log.info("HUMAN BRAIN pair=%s regime=%s direction=%s score=%.2f expiry=%s aligned=%s conflicts=%s",memory.get("asset",""),regime,direction,score,expiry,aligned,conflicts)
    if not approve:log.info("HUMAN GATE REJECT pair=%s reason=%s",memory.get("asset",""),reason)
    return {"approve":approve,"direction":direction,"score":score,"expiry":expiry,"regime":regime,"reason":reason}

def choose_expiry(s:dict,frames:dict|None=None)->int:
    frames=frames or {};agreement=int(s.get("indicator_agreement",0));strength=float(s.get("strength",0));adx=float(s.get("adx14",0));body=float(s.get("body_ratio",0));direction=s.get("direction","")
    if agreement>=4 and strength>=.8 and adx>=35 and body>=.55:return 1
    if agreement>=4 and strength>=.8 and adx>=30:return 2
    if agreement>=4 and strength>=.8 and adx>=22:return 3
    if agreement>=3 and strength>=.8 and adx>=18:return 5
    f10=frames.get("10m",{});f15=frames.get("15m",{})
    if direction and f10.get("direction")==direction and f15.get("direction")==direction:
        adx10=float(f10.get("adx14",0) or 0);adx15=float(f15.get("adx14",0) or 0)
        if strength>=.8 and min(adx10,adx15)>=25:return 15
        if strength>=.6 and min(adx10,adx15)>=18:return 10
    if agreement>=3 and strength>=.6:return 10
    return 0

def _ai_prompt(s,memory):
    return f'''You are Candice, a conservative professional fixed-time market analyst. Manual alerts only; never place trades. Do not claim certainty. Approve only fresh multi-indicator alignment with no material conflict. Choose expiry from 1, 2, 3, 5, 10, 15 minutes. Shorter duration is for strong immediate momentum; 10/15 only with higher-timeframe support. Reject weak, stale, contradictory, overextended or reversal-risk setups. Return ONLY JSON with decision, direction, confidence, expiry, reason. Technical evidence: {json.dumps(s)}. Historical memory: {json.dumps(memory)[:5000]}'''

def _parse_ai_content(content):
    try:
        text=str(content or "").strip();start=text.find("{");end=text.rfind("}")
        if start<0 or end<start:return None
        obj=json.loads(text[start:end+1]);return obj if isinstance(obj,dict) else None
    except Exception:return None

def _request_ai(url,key,model,ai_input):
    payload={"model":model,"messages":[{"role":"user","content":_ai_prompt(ai_input.get("technical",ai_input),ai_input.get("memory",{}))}],"temperature":.1,"max_completion_tokens":400,"response_format":{"type":"json_object"}}
    if "gpt-oss" in model.lower():payload["include_reasoning"]=False;payload["reasoning_effort"]="low"
    r=requests.post(url,headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},json=payload,timeout=AI_TIMEOUT)
    if not r.ok:raise RuntimeError(f"HTTP {r.status_code}: {r.text.replace(chr(10),' ')[:600]}")
    choices=r.json().get("choices") or []
    if not choices:raise ValueError("AI response has no choices")
    parsed=_parse_ai_content((choices[0].get("message") or {}).get("content",""))
    if not parsed:raise ValueError("AI returned invalid JSON")
    return parsed

def ai_review(snapshot,memory):
    providers=(("GROQ",os.getenv("GROQ_API_KEY","").strip(),os.getenv("GROQ_MODEL","openai/gpt-oss-120b").strip(),"https://api.groq.com/openai/v1/chat/completions"),("CEREBRAS",os.getenv("CEREBRAS_API_KEY","").strip(),os.getenv("CEREBRAS_MODEL","gpt-oss-120b").strip(),"https://api.cerebras.ai/v1/chat/completions"));attempted=False
    for name,key,model,url in providers:
        if not key:continue
        attempted=True
        try:
            result=_request_ai(url,key,model,{"technical":snapshot,"memory":memory});log.info("AI PROVIDER=%s MODEL=%s DECISION=%s CONFIDENCE=%s EXPIRY=%s",name,model,result.get("decision",""),result.get("confidence",0),result.get("expiry",0));return result
        except Exception as exc:log.warning("AI provider %s unavailable: %s",name,exc)
    if not attempted:return {"decision":"REJECT","confidence":0,"reason":"Groq/Cerebras AI provider not configured"}
    return {"decision":"REJECT","confidence":0,"reason":"Groq and Cerebras AI review unavailable"}

async def analyze(asset,broker,memory):
    df,err=await broker.candles(asset,60,ANALYSIS_CANDLE_COUNT,360)
    if err or df is None or len(df)<120:return Analysis(asset,"REJECT",reason=err or "insufficient fresh candles")
    base=closed_1m(df)
    if len(base)<120:return Analysis(asset,"REJECT",reason=f"insufficient closed candles ({len(base)})")
    try:
        snap1=technical_snapshot(base);frames={"1m":snap1}
        for mins in (3,5,10,15):
            agg=resample_ohlc(base,mins)
            if len(agg)>=60:frames[f"{mins}m"]=technical_snapshot(agg)
    except Exception as exc:return Analysis(asset,"REJECT",reason=f"technical calculation failed: {exc}")
    primary=frames.get("5m",snap1);direction=primary["direction"]
    if not direction or snap1["direction"]!=direction:return Analysis(asset,"REJECT",direction=direction,score=primary["strength"],reason="1m/5m direction conflict",timeframe="1m+5m")
    brain=human_brain(frames,{**(memory or {}),"asset":asset})
    if not brain["approve"]:return Analysis(asset,"REJECT",direction=direction,expiry=brain["expiry"],score=brain["score"],reason=f"Human brain WAIT: {brain['reason']}",timeframe="1m+3m+5m+10m+15m")
    context_agreement=sum(1 for k in ("10m","15m") if k in frames and frames[k]["direction"]==direction);context_conflict=sum(1 for k in ("10m","15m") if k in frames and frames[k]["direction"] and frames[k]["direction"]!=direction);technical_expiry=choose_expiry(primary,frames);candidate=brain["expiry"] if brain["expiry"] in EXPIRIES else technical_expiry
    if not candidate:return Analysis(asset,"REJECT",direction=direction,score=brain["score"],reason="No safe expiry",timeframe="1m+5m")
    if context_conflict>=2 and primary["strength"]<1.0:return Analysis(asset,"REJECT",direction=direction,expiry=candidate,score=brain["score"],reason="Higher-timeframe context conflicts",timeframe="1m+5m+10m+15m")
    ai_input={"1m":snap1,"3m":frames.get("3m",{}),"5m":primary,"10m":frames.get("10m",{}),"15m":frames.get("15m",{}),"human_brain":brain,"context_agreement":context_agreement,"context_conflict":context_conflict,"candidate_expiry":candidate};ai=await asyncio.to_thread(ai_review,ai_input,memory or {});decision=str(ai.get("decision","")).upper();ai_direction=str(ai.get("direction","")).upper();raw_conf=float(ai.get("confidence",0) or 0);conf=int(round(raw_conf*100)) if 0 <= raw_conf <= 1 else int(round(raw_conf));conf=max(0,min(100,conf));ai_exp=int(ai.get("expiry",0) or 0)
    if decision!="APPROVE" or ai_direction!=direction or conf<MIN_CONF or ai_exp not in EXPIRIES:return Analysis(asset,"REJECT",direction=ai_direction,confidence=conf,expiry=ai_exp,score=brain["score"],reason=str(ai.get("reason","AI gate rejected")),timeframe="1m+3m+5m+10m+15m")
    evidence=tuple(name for name,value in (("Parabolic SAR Reversal",primary["psar_direction"]),("Moving Average Crossover",primary["ma_crossover"]),("Donchian Channel Breakout",primary["donchian_breakout"]),("MACD Crossover",primary["macd_crossover"]),("Rate of Change Crossover",primary["roc_direction"])) if value==direction)
    return Analysis(asset,"APPROVE",direction=direction,confidence=conf,expiry=ai_exp,score=brain["score"],reason=str(ai.get("reason","Human brain + multi-timeframe technical + AI alignment")),timeframe="1m+3m+5m+10m+15m",evidence=evidence)

def session_state(now=None):
    now=now or datetime.now(timezone.utc);return "SIGNAL" if now.hour%3<2 else "RESEARCH"

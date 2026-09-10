"""Final reference-style technical classifier.
No trade execution.
"""
import sys, threading, time
import pandas as pd

def _app():
    m=sys.modules.get("__main__")
    if m is not None and getattr(m,"__file__","").endswith("app.py"): return m
    return sys.modules.get("app")

def _psar(high, low, close, step=.02, maximum=.2):
    h=high.astype(float).reset_index(drop=True); l=low.astype(float).reset_index(drop=True); n=len(h)
    if n<3: return pd.Series([float(l.iloc[0])]*n)
    out=[float(l.iloc[0])]; bull=True; af=step; ep=float(h.iloc[0])
    for i in range(1,n):
        v=out[-1]+af*(ep-out[-1])
        if bull:
            v=min(v,float(l.iloc[i-1]),float(l.iloc[i-2]) if i>1 else float(l.iloc[i-1]))
            if float(l.iloc[i])<v: bull=False; v=ep; ep=float(l.iloc[i]); af=step
            elif float(h.iloc[i])>ep: ep=float(h.iloc[i]); af=min(maximum,af+step)
        else:
            v=max(v,float(h.iloc[i-1]),float(h.iloc[i-2]) if i>1 else float(h.iloc[i-1]))
            if float(h.iloc[i])>v: bull=True; v=ep; ep=float(h.iloc[i]); af=step
            elif float(l.iloc[i])<ep: ep=float(l.iloc[i]); af=min(maximum,af+step)
        out.append(v)
    return pd.Series(out,index=close.index)

def _cross(a,b,up=True):
    if len(a)<2 or len(b)<2 or pd.isna(a.iloc[-1]) or pd.isna(b.iloc[-1]) or pd.isna(a.iloc[-2]) or pd.isna(b.iloc[-2]): return False
    return (float(a.iloc[-1])>float(b.iloc[-1]) and float(a.iloc[-2])<=float(b.iloc[-2])) if up else (float(a.iloc[-1])<float(b.iloc[-1]) and float(a.iloc[-2])>=float(b.iloc[-2]))

def _safe_scalar(value, default):
    try:
        if isinstance(value,pd.Series): value=value.iloc[-1]
        if pd.isna(value): return default
        return float(value)
    except Exception: return default

def _install():
    a=_app()
    if not a or getattr(a,"_REFERENCE_INDICATOR_METHOD_V2",False): return bool(a)
    original=getattr(a,"analyze_pair",None)
    if not callable(original): return False
    def analyze_pair(pair,df):
        # The legacy analyzer can raise pandas' "truth value of a Series is
        # ambiguous" error. It is optional enrichment only; never let it block
        # the reference indicator engine from reaching AI.
        try:
            base=original(pair,df)
            if not isinstance(base,dict): base={}
        except Exception as exc:
            base={}
            try: a.log.warning("INDICATOR BASE ANALYZER BYPASSED for %s: %s",pair,str(exc)[:140])
            except Exception: pass
        if len(df)<65: return base
        close=df.close.astype(float); high=df.high.astype(float); low=df.low.astype(float)
        sma4=close.rolling(4).mean(); sma60=close.rolling(60).mean()
        ema9=close.ewm(span=9,adjust=False).mean(); ema21=close.ewm(span=21,adjust=False).mean()
        ema12=close.ewm(span=12,adjust=False).mean(); ema26=close.ewm(span=26,adjust=False).mean(); macd=ema12-ema26; macds=macd.ewm(span=9,adjust=False).mean()
        roc9=close.pct_change(9)*100; zero=pd.Series(0.0,index=close.index)
        sar=_psar(high,low,close); dc_u=high.shift(1).rolling(20).max(); dc_l=low.shift(1).rolling(20).min()
        atr=(high-low).rolling(14).mean()
        c=float(close.iloc[-1]); prev=float(close.iloc[-2])
        vals={
          "psar":"UP" if c>float(sar.iloc[-1]) else "DOWN",
          "sma":"UP" if float(sma4.iloc[-1])>float(sma60.iloc[-1]) else "DOWN",
          "ema":"UP" if float(ema9.iloc[-1])>float(ema21.iloc[-1]) else "DOWN",
          "macd":"UP" if float(macd.iloc[-1])>float(macds.iloc[-1]) else "DOWN",
          "roc":"UP" if float(roc9.iloc[-1])>0 else "DOWN",
          "donchian":"UP" if pd.notna(dc_u.iloc[-1]) and c>float(dc_u.iloc[-1]) else ("DOWN" if pd.notna(dc_l.iloc[-1]) and c<float(dc_l.iloc[-1]) else "NEUTRAL")
        }
        up=sum(v=="UP" for v in vals.values()); down=sum(v=="DOWN" for v in vals.values())
        direction="UP" if up>down else ("DOWN" if down>up else "NO SIGNAL"); votes=max(up,down)
        ma_cross="UP" if _cross(sma4,sma60,True) else ("DOWN" if _cross(sma4,sma60,False) else "NONE")
        ema_cross="UP" if _cross(ema9,ema21,True) else ("DOWN" if _cross(ema9,ema21,False) else "NONE")
        macd_cross="UP" if _cross(macd,macds,True) else ("DOWN" if _cross(macd,macds,False) else "NONE")
        roc_cross="UP" if _cross(roc9,zero,True) else ("DOWN" if _cross(roc9,zero,False) else "NONE")
        psar_rev=vals["psar"] != ("UP" if prev>float(sar.iloc[-2]) else "DOWN")
        body=abs(c-prev); rng=max(float(high.iloc[-1]-low.iloc[-1]),1e-12); body_ratio=body/rng
        candle="BULLISH" if c>prev else ("BEARISH" if c<prev else "FLAT")
        candle_trigger=(direction=="UP" and candle=="BULLISH") or (direction=="DOWN" and candle=="BEARISH")
        adx=_safe_scalar(base.get("adx",20),20); rsi=_safe_scalar(base.get("rsi",50),50)
        score=votes*10
        score += 10 if candle_trigger else 0
        score += 5 if body_ratio>=.55 else 0
        score += 5 if adx>=18 else 0
        score += 5 if ((direction=="UP" and rsi<70) or (direction=="DOWN" and rsi>30)) else 0
        score += 5 if (ma_cross==direction or ema_cross==direction or macd_cross==direction) else 0
        score=min(100,score)
        core_ok=votes>=3 and abs(up-down)>=1 and (candle_trigger or score>=40)
        strong=votes>=4 and abs(up-down)>=2
        signal=direction if (core_ok or strong) else "NO SIGNAL"
        patterns=[]
        patterns.append("Parabolic SAR Reversal" if psar_rev else ("Parabolic SAR Bullish" if vals["psar"]=="UP" else "Parabolic SAR Bearish"))
        patterns.append("Moving Average Crossover" if ma_cross!="NONE" else "Moving Average Trend")
        patterns.append("EMA Moving Average Crossover" if ema_cross!="NONE" else "EMA Trend")
        patterns.append("Donchian Channel Breakout" if vals["donchian"] in ("UP","DOWN") else "Donchian Channel Structure")
        patterns.append("MACD Crossover" if macd_cross!="NONE" else "MACD Momentum")
        patterns.append("Rate of Change Crossover" if roc_cross!="NONE" else "Rate of Change Momentum")
        result=dict(base)
        result.update({"signal":signal,"confidence":int(score),"indicator_votes":votes,"indicator_total":6,"indicator_direction":direction,"indicator_method":"PSAR + SMA4/60 + EMA9/21 + Donchian20 + MACD12/26/9 + ROC9","indicator_values":vals,"ma_cross":ma_cross,"ema_cross":ema_cross,"macd_cross":macd_cross,"roc_cross":roc_cross,"psar_reversal":psar_rev,"donchian_breakout":vals["donchian"],"candle_direction":candle,"candle_body_ratio":round(body_ratio,3),"atr14":_safe_scalar(atr.iloc[-1],0),"rsi":rsi,"adx":adx,"patterns":patterns,"setup_score":int(score),"reason":f"{votes}/6 core indicators; candle={candle}; ADX={adx:.1f}; RSI={rsi:.1f}"})
        return result
    a.analyze_pair=analyze_pair; a._REFERENCE_INDICATOR_METHOD_V2=True
    a.log.warning("FINAL INDICATOR METHOD ACTIVE: PSAR + SMA4/60 + EMA9/21 + Donchian20 + MACD12/26/9 + ROC9 + candle trigger; legacy analyzer errors isolated")
    return True

def _boot():
    for _ in range(1800):
        try:
            if _install(): return
        except Exception: pass
        time.sleep(.1)
threading.Thread(target=_boot,name="final-indicator-method",daemon=True).start()

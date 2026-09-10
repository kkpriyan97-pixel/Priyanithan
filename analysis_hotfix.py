"""Legacy pandas-series compatibility hotfix.
The final reference indicator engine owns analyze_pair when active.
No broker order/trade execution is added.
"""
import math
import sys
import threading
import time

def _app_module():
    m=sys.modules.get("app")
    if m is not None: return m
    m=sys.modules.get("__main__")
    if m is not None and getattr(m,"__file__","").endswith("app.py"): return m
    return None

def _valid(value, default=0.0):
    try:
        value=float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default

def _fixed_analyze_pair(pair, df):
    a=_app_module(); close=df["close"]; high=df["high"]; low=df["low"]
    ema9=a.EMAIndicator(close,9).ema_indicator(); ema21=a.EMAIndicator(close,21).ema_indicator()
    macd_obj=a.MACD(close); macd=macd_obj.macd(); macd_signal=macd_obj.macd_signal()
    rsi=a.RSIIndicator(close,14).rsi(); stoch=a.StochasticOscillator(high,low,close).stoch(); adx=a.ADXIndicator(high,low,close).adx()
    bb=a.BollingerBands(close); bb_high=bb.bollinger_hband(); bb_low=bb.bollinger_lband()
    c=_valid(close.iloc[-1]); prev=_valid(close.iloc[-2],c); e9=_valid(ema9.iloc[-1]); e21=_valid(ema21.iloc[-1]); m=_valid(macd.iloc[-1]); ms=_valid(macd_signal.iloc[-1]); rv=_valid(rsi.iloc[-1],50); sv=_valid(stoch.iloc[-1],50); av=_valid(adx.iloc[-1]); bh=_valid(bb_high.iloc[-1],c); bl=_valid(bb_low.iloc[-1],c)
    up=(2 if e9>e21 else 0)+(2 if m>ms else 0)+(1 if 52<=rv<70 else 0)+(1 if c<bl else 0)+(1 if sv<20 else 0)
    down=(2 if e9<e21 else 0)+(2 if m<ms else 0)+(1 if 30<rv<=48 else 0)+(1 if c>bh else 0)+(1 if sv>80 else 0)
    signal="UP" if up>down else ("DOWN" if down>up else "NO SIGNAL")
    return {"pair":pair,"signal":signal,"confidence":int(min(99,50+abs(up-down)*7+max(0,av-20))),"candle_time":a.format_uae_timestamp(float(df["timestamp"].iloc[-1])),"patterns":["bullish close" if c>prev else "bearish close"],"trend":"BULLISH" if up>down else ("BEARISH" if down>up else "MIXED"),"rsi":rv,"adx":av,"reason":"technical structure","price":c}

def _patch():
    a=_app_module()
    if a is None or not hasattr(a,"analyze_pair"): return False
    # The final reference engine must remain the last owner of analyze_pair.
    if getattr(a,"_REFERENCE_INDICATOR_METHOD_V2",False):
        return True
    if getattr(a,"_ANALYSIS_SERIES_HOTFIX",False): return True
    a.analyze_pair=_fixed_analyze_pair; a._ANALYSIS_SERIES_HOTFIX=True
    a.log.warning("LEGACY ANALYSIS HOTFIX ACTIVE: Bollinger bands use scalar latest values")
    return True

def _boot():
    for _ in range(1800):
        try:
            if _patch(): return
        except Exception: pass
        time.sleep(.1)
threading.Thread(target=_boot,name="analysis-series-hotfix",daemon=True).start()

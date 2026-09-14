from __future__ import annotations
import json, logging, math, os, threading, time
from collections import deque
from datetime import datetime, timezone, timedelta

import requests
from flask import Flask, jsonify, request
from olymp_live import OlympLiveFeed

VERSION = "12.1-CANDICE-READONLY-STABLE"
AUTO_TRADE = False
MARTINGALE = False
EXPIRIES = (2, 3, 5, 10, 15)
MIN_CONFIDENCE = max(55, min(85, int(os.getenv("MIN_CONFIDENCE", "58"))))
MAX_DAILY_LOSSES = max(1, int(os.getenv("DAILY_MAX_LOSSES", "5")))
MAX_CONSECUTIVE_LOSSES = max(1, int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3")))
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SECRET = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "").strip()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("candice")
app = Flask(__name__)
candles: dict[str, deque] = {}
sent = set()
risk = {"date": "", "signals": 0, "wins": 0, "losses_total": 0, "losses": 0, "streak": 0}
live_feed: OlympLiveFeed | None = None
last_minute_seen: dict[str, int] = {}
lock = threading.RLock()


def uae_date(): return (datetime.now(timezone.utc) + timedelta(hours=4)).date().isoformat()
def reset_risk():
    d = uae_date()
    if risk["date"] != d: risk.update(date=d, signals=0, wins=0, losses_total=0, losses=0, streak=0)
def f(v):
    try: return float(v)
    except Exception: return None


def normalize(raw):
    out = []
    if not isinstance(raw, list): return out
    for x in raw[-300:]:
        if not isinstance(x, dict): continue
        o,h,l,c = f(x.get("open",x.get("o"))),f(x.get("high",x.get("h"))),f(x.get("low",x.get("l"))),f(x.get("close",x.get("c")))
        t=f(x.get("timestamp",x.get("time",x.get("t",time.time()))))
        if None in (o,h,l,c) or h < max(o,c) or l > min(o,c) or h < l: continue
        out.append({"open":o,"high":h,"low":l,"close":c,"timestamp":t or time.time()})
    return out


def ema(v,p):
    if len(v)<p:return None
    k,z=2.0/(p+1),sum(v[:p])/p
    for x in v[p:]: z=x*k+z*(1-k)
    return z

def rsi(v,p=14):
    if len(v)<=p:return None
    gains=[];losses=[]
    for a,b in zip(v[-p-1:-1],v[-p:]):
        d=b-a;gains.append(max(d,0));losses.append(max(-d,0))
    g,l=sum(gains)/p,sum(losses)/p
    return 100.0 if l==0 else 100.0-100.0/(1.0+g/l)

def atr(data,p=14):
    if len(data)<=p:return None
    trs=[max(b["high"]-b["low"],abs(b["high"]-a["close"]),abs(b["low"]-a["close"])) for a,b in zip(data[-p-1:-1],data[-p:])]
    return sum(trs)/p

def macd(v):
    if len(v)<40:return None,None,None
    line=[]
    for i in range(26,len(v)+1):
        a,b=ema(v[:i],12),ema(v[:i],26)
        if a is not None and b is not None:line.append(a-b)
    sig=ema(line,9) if len(line)>=9 else None
    return line[-1],sig,(line[-1]-sig) if sig is not None else None

def stochastic(data,p=14,signal=3):
    if len(data)<p+signal:return None,None
    ks=[]
    for i in range(p,len(data)+1):
        w=data[i-p:i];hi=max(x["high"] for x in w);lo=min(x["low"] for x in w)
        ks.append(50.0 if hi==lo else 100.0*(w[-1]["close"]-lo)/(hi-lo))
    return ks[-1],sum(ks[-signal:])/signal

def adx(data,p=14):
    if len(data)<p*2+2:return None,None,None
    tr=[];plus=[];minus=[]
    for a,b in zip(data[1:],data[2:]):
        up,dn=b["high"]-a["high"],a["low"]-b["low"]
        tr.append(max(b["high"]-b["low"],abs(b["high"]-a["close"]),abs(b["low"]-a["close"])))
        plus.append(up if up>dn and up>0 else 0.0);minus.append(dn if dn>up and dn>0 else 0.0)
    dx=[];dirs=[]
    for i in range(p,len(tr)+1):
        tv=sum(tr[i-p:i]);pi,mi=(0,0) if tv==0 else (100*sum(plus[i-p:i])/tv,100*sum(minus[i-p:i])/tv)
        dirs.append((pi,mi));dx.append(100*abs(pi-mi)/(pi+mi) if pi+mi else 0.0)
    return (sum(dx[-p:])/p,dirs[-1][0],dirs[-1][1]) if len(dx)>=p else (None,None,None)

def aggregate(data,n):
    if len(data)<n:return []
    buckets={}
    for x in data:buckets.setdefault(int(float(x["timestamp"]))//60//n,[]).append(x)
    return [{"open":w[0]["open"],"high":max(x["high"] for x in w),"low":min(x["low"] for x in w),"close":w[-1]["close"],"timestamp":w[-1]["timestamp"]} for k in sorted(buckets) if len((w:=buckets[k]))>=n]

def timeframe_score(data):
    if len(data)<25:return 0,0,{"reason":"insufficient"}
    c=[x["close"] for x in data];e9,e21,e50=ema(c,9),ema(c,21),ema(c,50);rv=rsi(c);ml,ms,md=macd(c);ax,dip,dim=adx(data);score=0
    if e9 and e21:score+=2 if e9>e21 else -2
    if e21 and e50:score+=2 if e21>e50 else -2
    if rv is not None:
        if 52<=rv<72:score+=1
        elif 28<rv<=48:score-=1
    if md is not None:score+=2 if md>0 else -2
    if ml is not None and ms is not None:score+=1 if ml>ms else -1
    if ax is not None and ax>=18:score+=1 if dip>dim else -1
    last,prev=data[-1],data[-2]
    if last["close"]>last["open"] and last["close"]>=prev["close"]:score+=1
    if last["close"]<last["open"] and last["close"]<=prev["close"]:score-=1
    return (1 if score>0 else -1 if score<0 else 0),score,{"EMA9":e9,"EMA21":e21,"EMA50":e50,"RSI":rv,"ADX":ax,"DI+":dip,"DI-":dim}

def pattern_signal(last,prev):
    body=abs(last["close"]-last["open"]);rng=max(last["high"]-last["low"],1e-12);upper=last["high"]-max(last["open"],last["close"]);lower=min(last["open"],last["close"])-last["low"]
    if body/rng<.25 and lower/rng>.55:return 1,"hammer"
    if body/rng<.25 and upper/rng>.55:return -1,"shooting star"
    if last["close"]>last["open"] and prev["close"]<prev["open"] and last["close"]>=prev["open"] and last["open"]<=prev["close"]:return 1,"bullish engulfing"
    if last["close"]<last["open"] and prev["close"]>prev["open"] and last["open"]>=prev["close"] and last["close"]<=prev["open"]:return -1,"bearish engulfing"
    return 0,"neutral"


def analyze(data):
    if len(data)<60:return {"decision":"NO_SIGNAL","confidence":0,"direction":None,"reasons":[f"warming history {len(data)}/60"]}
    c=[x["close"] for x in data];last,prev=data[-1],data[-2];e9,e21,e50=ema(c,9),ema(c,21),ema(c,50);rv,av=rsi(c),atr(data);ml,ms,md=macd(c);sk,ss=stochastic(data);ax,dip,dim=adx(data);support=min(x["low"] for x in data[-30:]);resistance=max(x["high"] for x in data[-30:]);score=0;reasons=[]
    if e9 and e21:score+=3 if e9>e21 else -3;reasons.append("EMA9/21 bullish" if e9>e21 else "EMA9/21 bearish")
    if e21 and e50:score+=2 if e21>e50 else -2;reasons.append("trend bullish" if e21>e50 else "trend bearish")
    if rv is not None:
        if 50<=rv<=70:score+=1;reasons.append(f"RSI {rv:.1f} bullish")
        elif 30<=rv<=50:score-=1;reasons.append(f"RSI {rv:.1f} bearish")
        elif rv>=78:score-=1;reasons.append(f"RSI {rv:.1f} overbought")
        elif rv<=22:score+=1;reasons.append(f"RSI {rv:.1f} oversold")
    if md is not None:score+=2 if md>0 else -2;reasons.append("MACD momentum +" if md>0 else "MACD momentum -")
    if ml is not None and ms is not None:score+=1 if ml>ms else -1
    if sk is not None and ss is not None:
        if sk>ss and sk<85:score+=1
        elif sk<ss and sk>15:score-=1
    if ax is not None and ax>=18:score+=2 if dip>dim else -2;reasons.append(f"ADX {ax:.1f} confirms {'bulls' if dip>dim else 'bears'}")
    if last["close"]>last["open"] and last["close"]>=prev["close"]:score+=1
    elif last["close"]<last["open"] and last["close"]<=prev["close"]:score-=1
    ps,pat=pattern_signal(last,prev);score+=2*ps
    if pat!="neutral":reasons.append(pat)
    if av:
        if abs(last["close"]-support)<=av*.45:score+=1;reasons.append("near support")
        if abs(resistance-last["close"])<=av*.45:score-=1;reasons.append("near resistance")
        if last["high"]-last["low"]<av*.25:score=int(score*.85);reasons.append("low volatility")
    d1,s1,_=timeframe_score(data);d3,s3,_=timeframe_score(aggregate(data,3));d5,s5,_=timeframe_score(aggregate(data,5))
    if d1:score+=2*d1
    if d3:score+=2*d3;reasons.append("3m confirms" if d3==d1 else "3m mixed")
    if d5:score+=2*d5;reasons.append("5m confirms" if d5==d1 else "5m mixed")
    direction=1 if score>0 else -1 if score<0 else 0;opposition=sum(1 for d in (d3,d5) if d and d==-d1);strong_entry=abs(s1)>=5 and d1!=0;veto=strong_entry and opposition==2 and abs(s3)>=4 and abs(s5)>=4;confidence=int(max(0,min(95,50+min(42,abs(score)*3.0))));quality=abs(s1)>=4 and (d3==d1 or d5==d1 or abs(s1)>=7);mtf={"1m":s1,"3m":s3,"5m":s5}
    if veto:return {"decision":"NO_SIGNAL","confidence":confidence,"direction":"UP" if d1>0 else "DOWN","reasons":reasons[-7:]+["strong 3m+5m opposition; setup rejected"],"mtf":mtf}
    if direction==0 or not quality or confidence<MIN_CONFIDENCE:return {"decision":"NO_SIGNAL","confidence":confidence,"direction":"UP" if direction>0 else "DOWN" if direction<0 else None,"reasons":reasons[-7:]+[f"adaptive quality gate: score={score}, 1m={s1}, 3m={s3}, 5m={s5}"],"mtf":mtf}
    return {"decision":"SIGNAL","confidence":confidence,"direction":"UP" if direction>0 else "DOWN","reasons":reasons[-8:]+[f"adaptive MTF score {score}"],"mtf":mtf,"indicators":{"EMA9":e9,"EMA21":e21,"EMA50":e50,"RSI":rv,"ATR":av,"MACD":ml,"MACD_signal":ms,"MACD_hist":md,"StochK":sk,"StochD":ss,"ADX":ax,"DI+":dip,"DI-":dim,"support":support,"resistance":resistance,"pattern":pat}}


def telegram(text):
    if not TOKEN:return False
    try:
        r=requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",json={"chat_id":CHAT_ID,"text":text,"disable_web_page_preview":True},timeout=15);r.raise_for_status();return True
    except requests.HTTPError as e:log.warning("Telegram send failed: HTTP %s",getattr(e.response,"status_code","?"));return False
    except Exception as e:log.warning("Telegram send failed: %s",type(e).__name__);return False


def send_signal(asset,data,tech,expiry=5):
    reset_risk()
    if risk["losses"]>=MAX_DAILY_LOSSES or risk["streak"]>=MAX_CONSECUTIVE_LOSSES:return {"ok":True,"status":"RISK_STOP"}
    expiry=min(EXPIRIES,key=lambda x:abs(x-int(expiry or 5)));ts,entry=data[-1]["timestamp"],data[-1]["close"];key=f"{asset}:{tech['direction']}:{expiry}:{int(ts//60)}"
    if key in sent:return {"ok":True,"status":"DUPLICATE_BLOCKED"}
    sent.add(key);risk["signals"]+=1;mtf,ind=tech.get("mtf",{}),tech.get("indicators",{})
    text=("━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • LIVE MARKET\n━━━━━━━━━━━━━━━━━━━━\n"f"🟢 SIGNAL • {tech['direction']}\n📈 Asset • {asset}\n💰 Entry • {entry:.6f}\n⏱️ Expiry • {expiry} min\n"f"🧠 Confidence • {tech['confidence']}%\n📊 MTF • 1m {mtf.get('1m','-')} | 3m {mtf.get('3m','-')} | 5m {mtf.get('5m','-')}\n"f"📌 RSI • {ind.get('RSI',0):.1f} | ADX • {ind.get('ADX',0):.1f}\n🧩 {', '.join(tech['reasons'][-5:])}\n""📡 Source • Olymp Trade live market data\n🛡️ READ-ONLY / DEMO / MANUAL ONLY\n🚫 Auto-trade OFF • Martingale OFF")
    telegram(text);return {"ok":True,"status":"SIGNAL","asset":asset,"direction":tech["direction"],"expiry":expiry,"confidence":tech["confidence"]}


def on_olymp_candle(asset,candle):
    with lock:
        q=candles.setdefault(asset,deque(maxlen=300));ts=int(candle.get("timestamp",time.time()))
        if last_minute_seen.get(asset)==ts:return
        q.append(candle);last_minute_seen[asset]=ts;data=list(q)
    tech=analyze(data)
    if tech["decision"]=="SIGNAL":
        result=send_signal(asset,data,tech,5);log.info("DECISION asset=%s signal=%s dir=%s conf=%s mtf=%s",asset,result.get("status"),tech.get("direction"),tech.get("confidence"),tech.get("mtf"))
    else:log.info("DECISION asset=%s NO_SIGNAL reason=%s mtf=%s",asset," | ".join(tech["reasons"][-2:]),tech.get("mtf"))

def seed_olymp_history(asset,candle):
    with lock:candles.setdefault(asset,deque(maxlen=300)).append(candle)

@app.get("/health")
def health():
    s=live_feed.status() if live_feed else {"connected":False,"assets":[]}
    reset_risk();return jsonify({"ok":True,"version":VERSION,"mode":"READ_ONLY_DEMO","timeframe":"1m live ticks + derived 3m/5m","expiries":list(EXPIRIES),"assets":list(candles.keys()),"telegram_configured":bool(TOKEN),"auto_trade":AUTO_TRADE,"martingale":MARTINGALE,"risk":risk.copy(),"brain":"adaptive multi-timeframe technical consensus","olymp":s})

@app.post("/webhook/tradingview")
def tradingview_webhook():
    if SECRET and request.headers.get("X-Webhook-Secret","")!=SECRET:return jsonify({"ok":False,"error":"unauthorized"}),401
    p=request.get_json(silent=True) or {};asset=str(p.get("asset",p.get("symbol","UNKNOWN"))).upper().strip();raw=normalize(p.get("candles",p.get("bars",[]))) or normalize([p])
    with lock:q=candles.setdefault(asset,deque(maxlen=300));q.extend(raw);data=list(q)
    tech=analyze(data);return jsonify(send_signal(asset,data,tech,p.get("expiry",5)) if tech["decision"]=="SIGNAL" else {"ok":True,"status":"NO_SIGNAL","technical":tech})


def main():
    global live_feed
    reset_risk()
    try:
        live_feed=OlympLiveFeed(on_olymp_candle,seed_olymp_history);live_feed.start();log.info("Olymp live feed started")
    except Exception as e:log.exception("Olymp live feed startup failed: %s",type(e).__name__)
    # Telegram commands are owned by sitecustomize's single poller. Do not start a second getUpdates loop.
    log.info("Telegram command listener delegated to single-owner runtime poller")
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")),threaded=True)

if __name__=="__main__":main()

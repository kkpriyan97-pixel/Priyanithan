from __future__ import annotations
import json,logging,os,time
from collections import defaultdict
from datetime import datetime,timezone,timedelta
from pathlib import Path
log=logging.getLogger("candice.brain")
EXPIRIES=(1,2,3,4,5,10,15)
STRATEGIES=("Trend Following","Breakout","Pullback","Support / Resistance","Candlestick","Momentum","Mean Reversion","Reversal","Multi-Timeframe","Volatility","Market Structure","Price Action")
class CandiceBrain:
 def __init__(self,send,feed):
  self.send=send;self.feed=feed;self.pending={};self.sent_cycles=set();self.learning_path=Path(os.getenv("CANDICE_LEARNING_FILE","candice_learning.json"));self.learning=self._load();self.stats=defaultdict(int)
 def _load(self):
  try:
   x=json.loads(self.learning_path.read_text());x.setdefault("setups",{});x.setdefault("assets",{});return x
  except Exception:return {"setups":{},"assets":{}}
 def _save(self):
  try:self.learning_path.write_text(json.dumps(self.learning,ensure_ascii=False,indent=2))
  except Exception as e:log.warning("LEARNING_SAVE_FAILED %s",type(e).__name__)
 @staticmethod
 def _ema(v,p):
  if len(v)<p:return None
  k=2/(p+1);x=sum(v[:p])/p
  for z in v[p:]:x=z*k+x*(1-k)
  return x
 @staticmethod
 def _rsi(v,p=14):
  if len(v)<=p:return None
  g=l=0.0
  for a,b in zip(v[-p-1:-1],v[-p:]):d=b-a;g+=max(d,0);l+=max(-d,0)
  return 100.0 if l==0 else 100-100/(1+g/l)
 @staticmethod
 def _atr(d,p=14):
  if len(d)<=p:return None
  return sum(max(b["high"]-b["low"],abs(b["high"]-a["close"]),abs(b["low"]-a["close"])) for a,b in zip(d[-p-1:-1],d[-p:]))/p
 @staticmethod
 def _adx(d,p=14):
  if len(d)<p*2+2:return None
  tr=[];plus=[];minus=[]
  for a,b in zip(d[1:],d[2:]):
   up=b["high"]-a["high"];dn=a["low"]-b["low"];tr.append(max(b["high"]-b["low"],abs(b["high"]-a["close"]),abs(b["low"]-a["close"])));plus.append(up if up>dn and up>0 else 0);minus.append(dn if dn>up and dn>0 else 0)
  dx=[]
  for i in range(p,len(tr)+1):
   tv=sum(tr[i-p:i]);pi=100*sum(plus[i-p:i])/tv if tv else 0;mi=100*sum(minus[i-p:i])/tv if tv else 0;dx.append(100*abs(pi-mi)/(pi+mi) if pi+mi else 0)
  return sum(dx[-p:])/p if len(dx)>=p else None
 @staticmethod
 def _macd(v):
  if len(v)<40:return None
  q=[]
  for i in range(26,len(v)+1):
   a=CandiceBrain._ema(v[:i],12);b=CandiceBrain._ema(v[:i],26)
   if a is not None and b is not None:q.append(a-b)
  s=CandiceBrain._ema(q,9) if len(q)>=9 else None
  return (q[-1],s,q[-1]-s) if s is not None else None
 @staticmethod
 def _aggregate(d,n):
  return [{"open":w[0]["open"],"high":max(x["high"] for x in w),"low":min(x["low"] for x in w),"close":w[-1]["close"],"timestamp":w[-1]["timestamp"]} for i in range(0,len(d)-n+1,n) for w in [d[i:i+n]]]
 def _trend(self,d,fast=9,slow=21):
  if len(d)<slow:return 0,0
  c=[x["close"] for x in d];ef=self._ema(c,fast);es=self._ema(c,slow);score=0
  if ef is not None and es is not None:score+=2 if ef>es else -2
  if len(d)>=10:score+=1 if c[-1]>c[-10] else -1
  adx=self._adx(d)
  if adx is not None and adx>=18:score+=1 if c[-1]>c[-10] else -1
  return (1 if score>0 else -1 if score<0 else 0),score
 def _pattern(self,d):
  if len(d)<3:return "Neutral",0
  a,b=d[-2],d[-1];body=abs(b["close"]-b["open"]);rng=max(b["high"]-b["low"],1e-12);up=b["high"]-max(b["open"],b["close"]);lo=min(b["open"],b["close"])-b["low"]
  if body/rng<.2 and lo/rng>.55:return "Hammer",1
  if body/rng<.2 and up/rng>.55:return "Rejection",-1
  if b["close"]>b["open"] and a["close"]<a["open"] and b["close"]>=a["open"] and b["open"]<=a["close"]:return "Bullish Engulfing",1
  if b["close"]<b["open"] and a["close"]>a["open"] and b["open"]>=a["close"] and b["close"]<=a["open"]:return "Bearish Engulfing",-1
  if body/rng<.12:return "Doji",0
  if b["high"]<=a["high"] and b["low"]>=a["low"]:return "Inside Bar",0
  if b["close"]>a["high"]:return "Breakout",1
  if b["close"]<a["low"]:return "Breakout",-1
  if b["close"]>b["open"] and b["close"]>a["close"]:return "Continuation",1
  if b["close"]<b["open"] and b["close"]<a["close"]:return "Continuation",-1
  return "Neutral",0
 def _history_rate(self,key):
  x=self.learning["setups"].get(key,{"n":0,"w":0,"l":0,"t":0});n=x["n"];return (x["w"]+.5*x["t"])/n if n else .5
 def analyze(self,asset,completed,live_price):
  if len(completed)<60 or live_price is None:return None
  d=list(completed[-60:]);last=dict(d[-1]);last["close"]=live_price;data=d[:-1]+[last];cl=[x["close"] for x in data];t1,s1=self._trend(data[-30:]);all15=self._aggregate(completed[-360:],15);t15,s15=self._trend(all15,5,13) if len(all15)>=13 else (0,0);rsi=self._rsi(cl);adx=self._adx(data);atr=self._atr(data);macd=self._macd(cl);pat,pdir=self._pattern(data);support=min(x["low"] for x in data[-30:]);resistance=max(x["high"] for x in data[-30:]);up=down=0;votes=[]
  def vote(name,direction,weight):
   nonlocal up,down
   if direction>0:up+=weight;votes.append(name+" UP")
   elif direction<0:down+=weight;votes.append(name+" DOWN")
  e9=self._ema(cl,9);e21=self._ema(cl,21);vote("Trend Following",1 if t1>0 and e9 and e21 and e9>e21 else -1 if t1<0 and e9 and e21 and e9<e21 else 0,3);hi=max(x["high"] for x in data[-20:]);lo=min(x["low"] for x in data[-20:]);vote("Breakout",1 if live_price>hi else -1 if live_price<lo else 0,3);vote("Pullback",1 if t15>0 and e21 and live_price<=e21 and live_price>data[-2]["close"] else -1 if t15<0 and e21 and live_price>=e21 and live_price<data[-2]["close"] else 0,3);vote("Support / Resistance",1 if atr and live_price-support<atr*.5 else -1 if atr and resistance-live_price<atr*.5 else 0,2);vote("Candlestick",pdir,3);vote("Momentum",1 if macd and macd[2]>0 else -1 if macd and macd[2]<0 else 0,3);vote("Mean Reversion",1 if rsi is not None and rsi<25 else -1 if rsi is not None and rsi>75 else 0,2);vote("Reversal",pdir if rsi is not None and ((pdir>0 and rsi<45) or (pdir<0 and rsi>55)) else 0,2);vote("Multi-Timeframe",t15,4);vote("Volatility",t15 if atr else 0,1);vote("Market Structure",t15,4);vote("Price Action",1 if live_price>data[-2]["close"] and last["close"]>last["open"] else -1 if live_price<data[-2]["close"] and last["close"]<last["open"] else 0,2)
  direction=1 if up>down else -1 if down>up else 0
  if not direction or up+down<10:return None
  if t15 and direction!=t15 and abs(s15)>=3:return None
  dname="UP" if direction>0 else "DOWN";ranked=[]
  for strategy in STRATEGIES:
   fit=3 if any(v.startswith(strategy+" "+dname) for v in votes) else 0
   for expiry in EXPIRIES:
    key=f"{asset}|{dname}|{pat}|{strategy}|{t15}|{expiry}";ranked.append((self._history_rate(key),fit,-abs(expiry-5),strategy,expiry))
  ranked.sort(reverse=True);_,_,_,strategy,expiry=ranked[0];key=f"{asset}|{dname}|{pat}|{strategy}|{t15}|{expiry}";confidence=int(max(58,min(96,58+(up+down)*1.15+min(10,abs(s15))*1.2+(self._history_rate(key)-.5)*12)))
  return {"asset":asset,"direction":dname,"confidence":confidence,"pattern":pat,"strategy":strategy,"expiry":expiry,"trend_15":"UP" if t15>0 else "DOWN" if t15<0 else "FLAT","structure_1m":"BULLISH" if t1>0 else "BEARISH" if t1<0 else "NEUTRAL","rsi":rsi or 0,"adx":adx or 0,"entry":live_price,"candle":last,"previous":d[-2],"votes":votes,"created":time.time()}
 def _message(self,s,target,now=None):
  st=self.feed.status();now=time.time() if now is None else now;local=datetime.fromtimestamp(now,timezone.utc)+timedelta(hours=4);tar=datetime.fromtimestamp(target,timezone.utc)+timedelta(hours=4);bal=st.get("account_balance");b="NOT_AVAILABLE" if bal is None else f"{bal:.2f} {st.get('account_currency','')}".strip();cd=max(0,int(target-now));return f"━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • LIVE MARKET\n━━━━━━━━━━━━━━━━━━━━\n📊 ASSET: {s['asset']}\n➡️ DIRECTION: {s['direction']}\n\n🕒 SIGNAL: {local.strftime('%H:%M:%S')} UAE\n🎯 TARGET: {tar.strftime('%H:%M:%S')} UAE\n⏳ SIGNAL COUNTDOWN: {cd:02d}\n\n⏱️ EXPIRY: {s['expiry']} MIN\n💰 ENTRY: {s['entry']:.6f}\n📈 15M TREND: {s['trend_15']}\n🕯️ 1M STRUCTURE: {s['structure_1m']}\n🧠 STRATEGY: {s['strategy']}\n🔎 PATTERN: {s['pattern']}\n📊 RSI: {s['rsi']:.1f} | ADX: {s['adx']:.1f}\n🧠 CONFIDENCE: {s['confidence']}%\n\n🟢 ACCOUNT: {st.get('account_mode','UNKNOWN')}\n💳 BALANCE: {b}\n\n🔐 READ-ONLY / MANUAL ONLY\n🚫 AUTO-TRADE: OFF\n🚫 MARTINGALE: OFF\n━━━━━━━━━━━━━━━━━━━━"
 def _countdown(self,s,target):
  mid=s.get("telegram_message_id")
  if not mid or not hasattr(self.send,"__self__"):return
  tg=self.send.__self__
  while time.time()<target:
   try:tg.edit(mid,self._message(s,target,time.time()))
   except Exception:pass
   time.sleep(1)
  try:tg.edit(mid,self._message(s,target,target))
  except Exception:pass
 def _result(self,s):
  if s.get("result") or time.time()<s["signal_time"]+s["expiry"]*60:return
  price=self.feed.live_price(s["asset"])
  if price is None:return
  diff=price-s["entry"];result="TIE" if abs(diff)<=max(abs(s["entry"])*1e-8,1e-10) else ("WIN" if (diff>0)==(s["direction"]=="UP") else "LOSS");s["exit"]=price;s["result"]=result;s["result_time"]=time.time();key=f"{s['asset']}|{s['direction']}|{s['pattern']}|{s['strategy']}|{s['trend_15']}|{s['expiry']}";x=self.learning["setups"].setdefault(key,{"n":0,"w":0,"l":0,"t":0});x["n"]+=1;x[{"WIN":"w","LOSS":"l","TIE":"t"}[result]]+=1;self._save();self.stats[result]+=1;st=self.feed.status();bal=st.get("account_balance");b="NOT_AVAILABLE" if bal is None else f"{bal:.2f} {st.get('account_currency','')}".strip();self.send(f"━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI RESULT\n━━━━━━━━━━━━━━━━━━━━\n📊 ASSET: {s['asset']}\n➡️ DIRECTION: {s['direction']}\n💰 ENTRY: {s['entry']:.6f}\n💰 EXIT: {price:.6f}\n⏱️ EXPIRY: {s['expiry']} MIN\n🏁 RESULT: {result}\n🕒 ENTRY: {datetime.fromtimestamp(s['signal_time'],timezone.utc).strftime('%H:%M:%S')} UAE\n🏁 RESULT: {datetime.fromtimestamp(s['result_time'],timezone.utc).strftime('%H:%M:%S')} UAE\n🧠 STRATEGY: {s['strategy']}\n📈 15M TREND: {s['trend_15']}\n💳 BALANCE: {b}\n🟢 ACCOUNT: {st.get('account_mode','UNKNOWN')}\n🧠 LEARNING: UPDATED\n━━━━━━━━━━━━━━━━━━━━")
 def on_candle(self,asset,candle):
  for s in list(self.pending.values()):
   if s["asset"]==asset:self._result(s)
 def _best_scan(self):
  candidates=[]
  for asset in self.feed.status().get("assets",[]):
   try:
    x=self.analyze(asset,self.feed.snapshot(asset),self.feed.live_price(asset))
    if x:candidates.append(x)
   except Exception:log.exception("ASSET_ANALYSIS_FAILED asset=%s",asset)
  candidates.sort(key=lambda x:(x["confidence"],-x["expiry"]),reverse=True);return (candidates[0] if candidates else None),len(candidates)
 def run(self):
  log.info("CANDICE_BRAIN_ON | ALL_ASSETS | LIVE_PRICE + LIVE_CANDLE | 1m + 1h + 15m | 5m cycles")
  while True:
   now=time.time();target=(int(now)//300+1)*300;signal_at=target-40;cycle=int(target//300)
   for s in list(self.pending.values()):self._result(s)
   while time.time()<signal_at:time.sleep(min(1,signal_at-time.time()))
   if cycle in self.sent_cycles:continue
   best,qualified=self._best_scan()
   if best:
    fresh=self.feed.live_price(best["asset"])
    if fresh is not None:
     best["entry"]=fresh;best["signal_time"]=time.time();best["target_time"]=target;key=f"{cycle}|{best['asset']}|{int(best['signal_time']//60)}"
     if key not in self.pending:
      self.pending[key]=best;msg=self.send(self._message(best,target))
      if isinstance(msg,dict) and msg.get("message_id"):
       best["telegram_message_id"]=msg["message_id"]
       import threading;threading.Thread(target=self._countdown,args=(best,target),daemon=True,name="candice-telegram-countdown").start()
      log.info("CANDICE_SIGNAL cycle=%s asset=%s direction=%s expiry=%sm entry=%s",cycle,best["asset"],best["direction"],best["expiry"],fresh)
   else:log.warning("CANDICE_NO_VALID_SETUP cycle=%s qualified=0",cycle)
   self.sent_cycles.add(cycle)
   while time.time()<target:
    for s in list(self.pending.values()):self._result(s)
    time.sleep(1)

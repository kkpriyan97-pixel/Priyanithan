from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("candice.brain")
EXPIRIES = (1, 2, 3, 4, 5, 10, 15)
STRATEGIES = ("Trend Following", "Breakout", "Pullback", "Support / Resistance", "Candlestick", "Momentum", "Mean Reversion", "Reversal", "Multi-Timeframe", "Volatility", "Market Structure", "Price Action")
UAE = timezone(timedelta(hours=4))


class CandiceBrain:
    def __init__(self, send, feed):
        self.send, self.feed = send, feed
        self.pending = {}
        self.sent_cycles = set()
        self.last_scan = {}
        self.learning_path = Path(os.getenv("CANDICE_LEARNING_FILE", "candice_learning.json"))
        self.learning = self._load()
        self.stats = defaultdict(int)
        self.day_key = self._day_key()
        self.daily_losses = 0
        self.consecutive_losses = 0
        self.max_daily_losses = max(1, int(os.getenv("DAILY_LOSS_LIMIT", "5")))
        self._load_daily()

    def _day_key(self): return datetime.now(UAE).strftime("%Y-%m-%d")
    def _load(self):
        try:
            x = json.loads(self.learning_path.read_text()); x.setdefault("setups", {}); x.setdefault("assets", {}); x.setdefault("daily", {}); return x
        except Exception: return {"setups": {}, "assets": {}, "daily": {}}
    def _load_daily(self):
        x = self.learning.get("daily", {}).get(self.day_key, {}); self.daily_losses = int(x.get("losses", 0)); self.consecutive_losses = int(x.get("consecutive_losses", 0))
    def _save(self):
        try: self.learning_path.write_text(json.dumps(self.learning, ensure_ascii=False, indent=2))
        except Exception as e: log.warning("LEARNING_SAVE_FAILED %s", type(e).__name__)
    def _reset_day_if_needed(self):
        k=self._day_key()
        if k != self.day_key: self.day_key=k; self.daily_losses=0; self.consecutive_losses=0
    def _blocked(self): self._reset_day_if_needed(); return self.daily_losses >= self.max_daily_losses or self.consecutive_losses >= 3
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
        for a,b in zip(d[:-1],d[1:]):
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
    def _aggregate_15m(d):
        groups={}
        for c in d:groups.setdefault((int(c["timestamp"])//900)*900,[]).append(c)
        out=[]
        for bucket in sorted(groups):
            w=sorted(groups[bucket],key=lambda x:x["timestamp"])
            if len(w)<12:continue
            out.append({"open":w[0]["open"],"high":max(x["high"] for x in w),"low":min(x["low"] for x in w),"close":w[-1]["close"],"timestamp":float(bucket)})
        return out
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
        x=self.learning["setups"].get(key,{"n":0,"w":0,"l":0,"t":0});n=int(x.get("n",0));return (x.get("w",0)+.5*x.get("t",0))/n if n else .5
    def analyze(self,asset,completed,live_price):
        if len(completed)<60 or live_price is None:return None
        d=list(completed[-60:]);cl=[x["close"] for x in d];t1,s1=self._trend(d[-30:]);all15=self._aggregate_15m(completed[-360:]);t15,s15=self._trend(all15,5,13) if len(all15)>=13 else (0,0);rsi=self._rsi(cl);adx=self._adx(d);atr=self._atr(d);macd=self._macd(cl);pat,pdir=self._pattern(d);support=min(x["low"] for x in d[-30:]);resistance=max(x["high"] for x in d[-30:]);up=down=0;votes=[]
        def vote(name,direction,weight):
            nonlocal up,down
            if direction>0:up+=weight;votes.append(name+" UP")
            elif direction<0:down+=weight;votes.append(name+" DOWN")
        e9,e21=self._ema(cl,9),self._ema(cl,21);vote("Trend Following",1 if t1>0 and e9>e21 else -1 if t1<0 and e9<e21 else 0,3);hi=max(x["high"] for x in d[-20:]);lo=min(x["low"] for x in d[-20:]);vote("Breakout",1 if live_price>hi else -1 if live_price<lo else 0,3);vote("Pullback",1 if t15>0 and e21 and live_price<=e21 and live_price>d[-1]["close"] else -1 if t15<0 and e21 and live_price>=e21 and live_price<d[-1]["close"] else 0,3);vote("Support / Resistance",1 if atr and live_price-support<atr*.5 else -1 if atr and resistance-live_price<atr*.5 else 0,2);vote("Candlestick",pdir,3);vote("Momentum",1 if macd and macd[2]>0 else -1 if macd and macd[2]<0 else 0,3);vote("Mean Reversion",1 if rsi is not None and rsi<25 else -1 if rsi is not None and rsi>75 else 0,2);vote("Reversal",pdir if rsi is not None and ((pdir>0 and rsi<45) or (pdir<0 and rsi>55)) else 0,2);vote("Multi-Timeframe",t15,4);vote("Volatility",t15 if atr else 0,1);vote("Market Structure",t15,4);vote("Price Action",1 if live_price>d[-1]["close"] else -1 if live_price<d[-1]["close"] else 0,2);direction=1 if up>down else -1 if down>up else 0
        if not direction or up+down<10:return None
        if t15 and direction!=t15 and abs(s15)>=3:return None
        dname="UP" if direction>0 else "DOWN";ranked=[]
        for strategy in STRATEGIES:
            fit=3 if any(v.startswith(strategy+" "+dname) for v in votes) else 0
            for expiry in EXPIRIES:
                key=f"{asset}|{dname}|{pat}|{strategy}|{t15}|{expiry}";ranked.append((self._history_rate(key),fit,-abs(expiry-5),strategy,expiry))
        ranked.sort(reverse=True);_,_,_,strategy,expiry=ranked[0];key=f"{asset}|{dname}|{pat}|{strategy}|{t15}|{expiry}";confidence=int(max(58,min(96,58+(up+down)*1.15+min(10,abs(s15))*1.2+(self._history_rate(key)-.5)*12)))
        return {"asset":asset,"direction":dname,"confidence":confidence,"pattern":pat,"strategy":strategy,"expiry":expiry,"trend_15":"UP" if t15>0 else "DOWN" if t15<0 else "FLAT","structure_1m":"BULLISH" if t1>0 else "BEARISH" if t1<0 else "NEUTRAL","rsi":rsi or 0,"adx":adx or 0,"entry":float(live_price),"candle":d[-1],"previous":d[-2],"votes":votes,"created":time.time()}
    def _message(self,s,target,now=None):
        st=self.feed.status();now=time.time() if now is None else now;local=datetime.fromtimestamp(now,timezone.utc).astimezone(UAE);tar=datetime.fromtimestamp(target,timezone.utc).astimezone(UAE);bal=st.get("account_balance");b="NOT_AVAILABLE" if bal is None else f"{bal:.2f} {st.get('account_currency','')}".strip();cd=max(0,int(target-now));return ("━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • LIVE MARKET\n━━━━━━━━━━━━━━━━━━━━\n" f"📊 ASSET: {s['asset']}\n➡️ DIRECTION: {s['direction']}\n\n" f"🕒 SIGNAL: {local.strftime('%H:%M:%S')} UAE\n🎯 TARGET: {tar.strftime('%H:%M:%S')} UAE\n⏳ SIGNAL COUNTDOWN: {cd:02d}\n\n" f"⏱️ EXPIRY: {s['expiry']} MIN\n💰 ENTRY: {s['entry']:.6f}\n📈 15M TREND: {s['trend_15']}\n🕯️ 1M STRUCTURE: {s['structure_1m']}\n" f"🧠 STRATEGY: {s['strategy']}\n🔎 PATTERN: {s['pattern']}\n📊 RSI: {s['rsi']:.1f} | ADX: {s['adx']:.1f}\n🧠 CONFIDENCE: {s['confidence']}%\n\n" f"🟢 ACCOUNT: {st.get('account_mode','UNKNOWN')}\n💳 BALANCE: {b}\n\n🔐 READ-ONLY / MANUAL ONLY\n🚫 AUTO-TRADE: OFF\n🚫 MARTINGALE: OFF\n━━━━━━━━━━━━━━━━━━━━")
    def _countdown(self,s,target):
        mid=s.get("telegram_message_id");tg=getattr(self.send,"__self__",None)
        if not mid or tg is None:return
        while time.time()<target and not s.get("result"):
            try:tg.edit(mid,self._message(s,target,time.time()))
            except Exception:pass
            time.sleep(1)
        try:tg.edit(mid,self._message(s,target,target))
        except Exception:pass
    def _result(self,s):
        if s.get("result") or time.time()<s["expiry_start"]:return False
        price=self.feed.live_price(s["asset"])
        if price is None:
            log.warning("RESULT_PRICE_UNAVAILABLE asset=%s",s["asset"]);return False
        diff=price-s["entry"];tolerance=max(abs(s["entry"])*1e-8,1e-10);result="TIE" if abs(diff)<=tolerance else ("WIN" if (diff>0)==(s["direction"]=="UP") else "LOSS")
        s.update(exit=price,result=result,result_time=time.time());key=f"{s['asset']}|{s['direction']}|{s['pattern']}|{s['strategy']}|{s['trend_15']}|{s['expiry']}";x=self.learning["setups"].setdefault(key,{"n":0,"w":0,"l":0,"t":0});x["n"]+=1;x[{"WIN":"w","LOSS":"l","TIE":"t"}[result]]+=1;self._reset_day_if_needed();self.stats[result]+=1
        if result=="LOSS":self.daily_losses+=1;self.consecutive_losses+=1
        elif result=="WIN":self.consecutive_losses=0
        self.learning["daily"][self.day_key]={"losses":self.daily_losses,"consecutive_losses":self.consecutive_losses,"signals":self.learning["daily"].get(self.day_key,{}).get("signals",0)};self._save();st=self.feed.status();bal=st.get("account_balance");b="NOT_AVAILABLE" if bal is None else f"{bal:.2f} {st.get('account_currency','')}".strip();msg=("━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI RESULT\n━━━━━━━━━━━━━━━━━━━━\n" f"📊 ASSET: {s['asset']}\n➡️ DIRECTION: {s['direction']}\n💰 ENTRY: {s['entry']:.6f}\n💰 EXIT: {price:.6f}\n⏱️ EXPIRY: {s['expiry']} MIN\n🏁 RESULT: {result}\n" f"🕒 SIGNAL: {datetime.fromtimestamp(s['signal_time'],timezone.utc).astimezone(UAE).strftime('%H:%M:%S')} UAE\n🎯 TARGET: {datetime.fromtimestamp(s['target_time'],timezone.utc).astimezone(UAE).strftime('%H:%M:%S')} UAE\n🏁 RESULT TIME: {datetime.fromtimestamp(s['result_time'],timezone.utc).astimezone(UAE).strftime('%H:%M:%S')} UAE\n" f"🧠 STRATEGY: {s['strategy']}\n📈 15M TREND: {s['trend_15']}\n💳 BALANCE: {b}\n🟢 ACCOUNT: {st.get('account_mode','UNKNOWN')}\n📉 DAILY LOSSES: {self.daily_losses}/{self.max_daily_losses}\n🔁 CONSECUTIVE LOSSES: {self.consecutive_losses}/3\n🧠 LEARNING: UPDATED\n━━━━━━━━━━━━━━━━━━━━");self.send(msg);log.info("RESULT asset=%s result=%s expiry=%s",s["asset"],result,s["expiry"]);return True
    def _result_worker(self,s):
        wait=max(0,s["expiry_start"]-time.time());time.sleep(wait)
        deadline=time.time()+max(30,int(os.getenv("RESULT_RETRY_SECONDS","90")))
        attempt=0
        while not s.get("result") and time.time()<=deadline:
            attempt+=1
            try:
                if self._result(s): return
            except Exception:
                log.exception("RESULT_RETRY_FAILED asset=%s attempt=%s",s.get("asset"),attempt)
            if s.get("result"): return
            time.sleep(2)
        if not s.get("result"): log.error("RESULT_UNRESOLVED asset=%s attempts=%s",s.get("asset"),attempt)
    def on_candle(self,asset,candle):
        try:
            scan=self.analyze(asset,self.feed.snapshot(asset),self.feed.live_price(asset))
            if scan:self.last_scan[asset]=scan;log.info("BRAIN_1M_ANALYSIS asset=%s direction=%s confidence=%s expiry=%s strategy=%s",asset,scan["direction"],scan["confidence"],scan["expiry"],scan["strategy"])
            else:self.last_scan.pop(asset,None);log.info("BRAIN_1M_ANALYSIS asset=%s decision=NO_SIGNAL",asset)
        except Exception:log.exception("BRAIN_CANDLE_ANALYSIS_FAILED asset=%s",asset)
        for s in list(self.pending.values()):
            if s["asset"]==asset and not s.get("result") and time.time()>=s["expiry_start"]:
                try:self._result(s)
                except Exception:log.exception("CANDLE_RESULT_CHECK_FAILED asset=%s",asset)
    def _best_scan(self):
        candidates=[]
        for asset in self.feed.status().get("assets",[]):
            try:
                x=self.analyze(asset,self.feed.snapshot(asset),self.feed.live_price(asset))
                if x:candidates.append(x)
            except Exception:log.exception("ASSET_ANALYSIS_FAILED asset=%s",asset)
        if not candidates:return None
        candidates.sort(key=lambda x:(x["confidence"],-x["expiry"]),reverse=True);return candidates[0]
    def run(self):
        while True:
            try:
                self._reset_day_if_needed();now=time.time();target=(int(now//300)+1)*300;decision_time=target-40
                if now>decision_time+1:target+=300;decision_time+=300
                time.sleep(max(0,decision_time-time.time()));cycle=int(target//300)
                if self._blocked():log.warning("BRAIN_BLOCKED cycle=%s daily_losses=%s consecutive_losses=%s",cycle,self.daily_losses,self.consecutive_losses);time.sleep(max(1,target-time.time()));continue
                if time.time()>decision_time+2:log.warning("SIGNAL_WINDOW_MISSED cycle=%s",cycle);time.sleep(max(1,target-time.time()));continue
                s=self._best_scan()
                if not s:log.info("SIGNAL_WINDOW_NO_SIGNAL target=%s",target);time.sleep(max(1,target-time.time()));continue
                fresh=self.feed.live_price(s["asset"])
                if fresh is None:log.warning("SIGNAL_ABORT reason=no_live_price asset=%s",s["asset"]);time.sleep(max(1,target-time.time()));continue
                dedupe=f"{s['asset']}|{s['expiry']}|{cycle}"
                if dedupe in self.sent_cycles:time.sleep(max(1,target-time.time()));continue
                s["entry"]=float(fresh);s["signal_time"]=time.time();s["target_time"]=float(target);s["expiry_start"]=float(target+s["expiry"]*60)
                r=self.send(self._message(s,target,s["signal_time"]))
                if not isinstance(r,dict) or not r.get("message_id"):log.warning("TELEGRAM_SIGNAL_NOT_CONFIRMED cycle=%s",cycle);time.sleep(max(1,target-time.time()));continue
                self.sent_cycles.add(dedupe);s["telegram_message_id"]=r["message_id"];key=f"{s['asset']}|{s['expiry']}|{cycle}";self.pending[key]=s;self.learning["daily"].setdefault(self.day_key,{"losses":self.daily_losses,"consecutive_losses":self.consecutive_losses,"signals":0});self.learning["daily"][self.day_key]["signals"]+=1;self._save();threading.Thread(target=self._countdown,args=(s,target),daemon=True).start();threading.Thread(target=self._result_worker,args=(s,),daemon=True).start();log.info("SIGNAL_CONFIRMED asset=%s direction=%s entry=%s signal=%s target=%s expiry=%s",s["asset"],s["direction"],s["entry"],datetime.fromtimestamp(s["signal_time"],timezone.utc).astimezone(UAE).strftime("%H:%M:%S"),datetime.fromtimestamp(target,timezone.utc).astimezone(UAE).strftime("%H:%M:%S"),s["expiry"]);time.sleep(max(1,target-time.time()))
            except Exception:log.exception("BRAIN_LOOP_ERROR");time.sleep(2)

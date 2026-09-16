from __future__ import annotations
import json, logging, os, threading, time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

LOG=logging.getLogger("candice.brain")
EXPIRIES=(1,2,3,4,5,10,15)
LEAD=40
SCAN=2
HISTORY=60
LEARNING=Path(os.getenv("CANDICE_LEARNING_FILE","candice_learning.json"))

class CandiceBrain:
    def __init__(self):
        self.lock=threading.RLock(); self.sent_cycles=set(); self.pending={}; self.last_expiry=None
        self.learning=self._load(); self.stats={"cycles":0,"signals":0,"wins":0,"losses":0,"ties":0,"scans":0}
    def _load(self):
        try:return json.loads(LEARNING.read_text()) if LEARNING.exists() else {"combos":{},"patterns":{},"assets":{}}
        except Exception:return {"combos":{},"patterns":{},"assets":{}}
    def _save(self):
        try:LEARNING.write_text(json.dumps(self.learning,ensure_ascii=False,indent=2))
        except Exception:pass
    @staticmethod
    def _rate(x):
        n=int(x.get("n",0)); return (int(x.get("w",0))+.5*int(x.get("t",0)))/n if n else .5
    def _learn(self,s,r):
        keys=[(self.learning["combos"],f"{s['asset']}|{s['strategy']}|{s['pattern']}|{s['expiry']}|{s['direction']}"),(self.learning["patterns"],f"{s['pattern']}|{s['strategy']}|{s['expiry']}|{s['direction']}"),(self.learning["assets"],s["asset"])]
        for group,key in keys:
            x=group.setdefault(key,{"n":0,"w":0,"l":0,"t":0});x["n"]+=1;x[{"WIN":"w","LOSS":"l","TIE":"t"}[r]]+=1
        self.stats["wins"]+=r=="WIN";self.stats["losses"]+=r=="LOSS";self.stats["ties"]+=r=="TIE";self._save()
    @staticmethod
    def _ema(v,p):
        if len(v)<p:return None
        k=2/(p+1);z=sum(v[:p])/p
        for x in v[p:]:z=x*k+z*(1-k)
        return z
    @staticmethod
    def _rsi(v,p=14):
        if len(v)<=p:return None
        g=l=0
        for a,b in zip(v[-p-1:-1],v[-p:]):d=b-a;g+=max(d,0);l+=max(-d,0)
        g/=p;l/=p;return 100 if l==0 else 100-100/(1+g/l)
    @staticmethod
    def _atr(d,p=14):
        if len(d)<=p:return None
        return sum(max(b["high"]-b["low"],abs(b["high"]-a["close"]),abs(b["low"]-a["close"])) for a,b in zip(d[-p-1:-1],d[-p:]))/p
    @staticmethod
    def _adx(d,p=14):
        if len(d)<p*2+2:return None,None,None
        tr=[];pu=[];mi=[]
        for a,b in zip(d[1:],d[2:]):
            u=b["high"]-a["high"];v=a["low"]-b["low"];tr.append(max(b["high"]-b["low"],abs(b["high"]-a["close"]),abs(b["low"]-a["close"])));pu.append(u if u>v and u>0 else 0);mi.append(v if v>u and v>0 else 0)
        dx=[];di=[]
        for i in range(p,len(tr)+1):
            tv=sum(tr[i-p:i]);a=100*sum(pu[i-p:i])/tv if tv else 0;b=100*sum(mi[i-p:i])/tv if tv else 0;di.append((a,b));dx.append(100*abs(a-b)/(a+b) if a+b else 0)
        return (sum(dx[-p:])/p,di[-1][0],di[-1][1]) if len(dx)>=p else (None,None,None)
    @staticmethod
    def _macd(v):
        if len(v)<40:return None,None,None
        q=[]
        for i in range(26,len(v)+1):
            a=CandiceBrain._ema(v[:i],12);b=CandiceBrain._ema(v[:i],26)
            if a is not None and b is not None:q.append(a-b)
        s=CandiceBrain._ema(q,9) if len(q)>=9 else None;return q[-1],s,q[-1]-s if s is not None else None
    @staticmethod
    def _agg(d,n):
        return [{"open":w[0]["open"],"high":max(x["high"] for x in w),"low":min(x["low"] for x in w),"close":w[-1]["close"],"timestamp":w[-1]["timestamp"]} for i in range(0,len(d)-n+1,n) for w in [d[i:i+n]]]
    def _trend(self,d):
        if len(d)<20:return 0,0
        c=[x["close"] for x in d];e9=self._ema(c,9);e21=self._ema(c,21);e50=self._ema(c,50);score=0
        if e9 and e21:score+=2 if e9>e21 else -2
        if e21 and e50:score+=2 if e21>e50 else -2
        adx,dp,dm=self._adx(d)
        if adx and adx>=18:score+=2 if dp>dm else -2
        return (1 if score>0 else -1 if score<0 else 0),score
    def _pattern(self,d):
        if len(d)<2:return "neutral",0
        a,b=d[-2],d[-1];body=abs(b["close"]-b["open"]);rng=max(b["high"]-b["low"],1e-12);up=b["high"]-max(b["open"],b["close"]);lo=min(b["open"],b["close"])-b["low"]
        if body/rng<.25 and lo/rng>.55:return "hammer",1
        if body/rng<.25 and up/rng>.55:return "shooting_star",-1
        if b["close"]>b["open"] and a["close"]<a["open"] and b["close"]>=a["open"] and b["open"]<=a["close"]:return "bullish_engulfing",1
        if b["close"]<b["open"] and a["close"]>a["open"] and b["open"]>=a["close"] and b["close"]<=a["open"]:return "bearish_engulfing",-1
        if b["close"]>b["open"] and b["close"]>a["close"]:return "bull_continuation",1
        if b["close"]<b["open"] and b["close"]<a["close"]:return "bear_continuation",-1
        return "neutral",0
    def _freq(self,d):
        q=defaultdict(int)
        for i in range(2,len(d)):q[self._pattern(d[:i+1])[0]]+=1
        return dict(q)
    def analyze(self,asset,data,forming):
        if len(data)<HISTORY:return None
        context=data[-300:];hour=data[-60:];live=list(hour)
        if forming and float(forming.get("timestamp",0))>=float(live[-1]["timestamp"]):live.append(dict(forming))
        c=[x["close"] for x in live];e9=self._ema(c,9);e21=self._ema(c,21);r=self._rsi(c);adx,dp,dm=self._adx(live);m,ms,mh=self._macd(c);atr=self._atr(live)
        t1,s1=self._trend(live[-30:]);t15,s15=self._trend(self._agg(context,15));pat,pdir=self._pattern(live);freq=self._freq(hour)
        votes={"trend_following":3,"breakout":3,"pullback":3,"support_resistance":2,"candlestick":3,"momentum":3,"mean_reversion":2,"reversal":2,"multi_timeframe":4,"volatility":1,"market_structure":4,"price_action":2};up=down=0;active=[]
        def v(name,d):
            nonlocal up,down
            if d>0:up+=votes[name];active.append(name+" UP")
            elif d<0:down+=votes[name];active.append(name+" DOWN")
        v("trend_following",1 if e9 and e21 and e9>e21 else -1 if e9 and e21 and e9<e21 else 0)
        hi=max(x["high"] for x in live[-20:]);lo=min(x["low"] for x in live[-20:]);v("breakout",1 if live[-1]["close"]>=hi else -1 if live[-1]["close"]<=lo else 0)
        v("pullback",1 if t15>0 and e21 and live[-1]["low"]<=e21<live[-1]["close"] else -1 if t15<0 and e21 and live[-1]["high"]>=e21>live[-1]["close"] else 0)
        support=min(x["low"] for x in live[-30:]);res=max(x["high"] for x in live[-30:]);v("support_resistance",1 if atr and live[-1]["close"]-support<atr*.5 else -1 if atr and res-live[-1]["close"]<atr*.5 else 0)
        v("candlestick",pdir);v("momentum",1 if m is not None and ms is not None and m>ms and mh>0 else -1 if m is not None and ms is not None and m<ms and mh<0 else 0);v("mean_reversion",1 if r is not None and r<25 else -1 if r is not None and r>75 else 0);v("reversal",pdir if r is not None and ((pdir>0 and r<45) or (pdir<0 and r>55)) else 0);v("multi_timeframe",t15);v("volatility",t15 if atr else 0);v("market_structure",t15);v("price_action",1 if live[-1]["close"]>live[-1]["open"] and live[-1]["close"]>=live[-2]["close"] else -1 if live[-1]["close"]<live[-1]["open"] and live[-1]["close"]<=live[-2]["close"] else 0)
        direction=1 if up>down else -1 if down>up else 0
        if not direction or up+down<9:return None
        if t15 and direction!=t15 and abs(s15)>=4:return None
        if t1 and direction!=t1 and abs(s1)>=6:return None
        dname="UP" if direction>0 else "DOWN"; strategy=max(votes,key=lambda x:votes[x]);best=(-1,strategy)
        for name,w in votes.items():
            h=self.learning["patterns"].get(f"{pat}|{name}|{dname}",{});score=w*(.5+self._rate(h))
            if score>best[0]:best=(score,name)
        strategy=best[1]
        base={"breakout":1,"candlestick":2,"pullback":3,"reversal":2,"mean_reversion":2,"support_resistance":3,"momentum":3,"trend_following":5,"multi_timeframe":5,"market_structure":5}.get(strategy,4)
        es={e:self._rate(self.learning["combos"].get(f"{asset}|{strategy}|{pat}|{e}|{dname}",{})) for e in EXPIRIES}
        ranked=sorted(EXPIRIES,key=lambda e:(es[e],1 if e==base else 0,-abs(e-base)),reverse=True)
        expiry=ranked[0]
        if self.last_expiry is not None and expiry==self.last_expiry:
            expiry=ranked[1] if len(ranked)>1 else expiry
        self.last_expiry=expiry
        confidence=int(max(58,min(96,58+min(25,(up+down)*1.2)+min(8,abs(s15))+(es[expiry]-.5)*15)))
        if confidence<58:return None
        return {"asset":asset,"direction":dname,"confidence":confidence,"strategy":strategy,"pattern":pat,"pattern_frequency":freq.get(pat,0),"expiry":expiry,"trend_15":"UP" if t15>0 else "DOWN" if t15<0 else "FLAT","trend_1":"UP" if t1>0 else "DOWN" if t1<0 else "FLAT","rsi":r or 0,"adx":adx or 0,"entry":float(live[-1]["close"]),"candle":dict(live[-1]),"previous_candle":dict(live[-2]),"reasons":active[-8:],"expiry_history":es,"created":time.time()}
    def _forming(self,feed,a):
        try:return dict((feed.forming or {}).get(a) or {}) or None
        except Exception:return None
    def _scan(self,candles,feed):
        try:assets=list((feed.status() or {}).get("assets") or [])
        except Exception:assets=[]
        out=[];scanned=0;recent={x["asset"] for x in self.pending.values() if time.time()-x.get("sent_at",0)<1800}
        for a in assets:
            d=list(candles.get(a,[]))
            if len(d)<HISTORY:continue
            scanned+=1
            try:
                x=self.analyze(a,d,self._forming(feed,a))
                if x:x["rank"]=x["confidence"]-(3 if a in recent else 0);out.append(x)
            except Exception:LOG.exception("asset analysis failed %s",a)
        self.stats["scans"]+=scanned;out.sort(key=lambda x:(x["rank"],x["confidence"],x["created"]),reverse=True);return out,len(assets),scanned
    def _message(self,s,feed,target):
        st=feed.status() if feed else {};bal=st.get("account_balance");money="NOT_AVAILABLE" if bal is None else f"{float(bal):.2f} {st.get('account_currency','')}".strip();now=datetime.now(timezone.utc)+timedelta(hours=4);t=datetime.fromtimestamp(target,timezone.utc)+timedelta(hours=4)
        return f"━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • LIVE MARKET\n━━━━━━━━━━━━━━━━━━━━\n📊 ASSET • {s['asset']}\n➡️ DIRECTION • {s['direction']}\n💰 ENTRY • {s['entry']:.6f}\n⏱️ EXPIRY • {s['expiry']} MIN\n🕒 SIGNAL • {now.strftime('%H:%M:%S')} UAE\n🎯 TARGET • {t.strftime('%H:%M:%S')} UAE\n⏳ COUNTDOWN • {max(0,int(target-time.time())):02d}s\n📈 15M TREND • {s['trend_15']}\n🕯️ 1M STRUCTURE • {s['trend_1']}\n🧠 STRATEGY • {s['strategy']}\n🔎 PATTERN • {s['pattern']} ({s['pattern_frequency']} in 1h)\n📊 RSI • {s['rsi']:.1f} | ADX • {s['adx']:.1f}\n🧠 CONFIDENCE • {s['confidence']}%\n💳 ACCOUNT • {st.get('account_mode','UNKNOWN')}\n💰 BALANCE • {money}\n🔐 READ-ONLY / MANUAL ONLY\n🚫 AUTO-TRADE • OFF\n🚫 MARTINGALE • OFF\n━━━━━━━━━━━━━━━━━━━━"
    def _result(self,s,candles,feed,send):
        if s.get("result") or time.time()<s["signal_time"]+s["expiry"]*60:return
        f=self._forming(feed,s["asset"]);price=float(f.get("close",0)) if f else 0
        if not price:
            d=candles.get(s["asset"],[]);price=float(d[-1]["close"]) if d else 0
        if not price:return
        diff=price-s["entry"];result="TIE" if abs(diff)<=max(abs(s["entry"])*1e-8,1e-10) else "WIN" if (diff>0)==(s["direction"]=="UP") else "LOSS";s["exit"]=price;s["result"]=result;self._learn(s,result)
        st=feed.status() if feed else {};send(f"━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • RESULT\n━━━━━━━━━━━━━━━━━━━━\n📊 ASSET • {s['asset']}\n➡️ DIRECTION • {s['direction']}\n💰 ENTRY • {s['entry']:.6f}\n💰 EXIT • {price:.6f}\n⏱️ EXPIRY • {s['expiry']} MIN\n🏁 RESULT • {result}\n🧠 STRATEGY • {s['strategy']}\n🔎 PATTERN • {s['pattern']}\n📈 15M TREND • {s['trend_15']}\n💳 ACCOUNT • {st.get('account_mode','UNKNOWN')}\n💰 BALANCE • {st.get('account_balance','NOT_AVAILABLE')}\n🧠 LEARNING • UPDATED\n━━━━━━━━━━━━━━━━━━━━")
        LOG.info("CANDICE_RESULT asset=%s result=%s entry=%s exit=%s expiry=%s",s['asset'],result,s['entry'],price,s['expiry'])
    def on_completed_candle(self,asset,candle,candles,feed,send):
        for s in list(self.pending.values()):
            if s["asset"]==asset:self._result(s,candles,feed,send)
        LOG.info("REAL_1M_RESEARCH asset=%s ts=%s close=%s",asset,int(candle["timestamp"]),candle["close"])
    def loop(self,candles,feed_getter,send):
        LOG.info("CANDICE_BRAIN started | DIRECT LIVE VIEW | all assets | 1m + 1h + 15m | 5m cycle | signal lead=40s | Dubai")
        while True:
            try:
                feed=feed_getter()
                if not feed:time.sleep(1);continue
                now=time.time();cycle=int(now//300);target=(cycle+1)*300;start=target-LEAD
                for s in list(self.pending.values()):self._result(s,candles,feed,send)
                if cycle in self.sent_cycles:time.sleep(1);continue
                if now<start:time.sleep(min(1,start-now));continue
                best=None
                # Direct-live selection begins 40s before the 5m target. No snapshot is taken.
                end=start+8
                while time.time()<end:
                    q,total,scanned=self._scan(candles,feed);LOG.info("LIVE_SCAN cycle=%s assets=%s scanned=%s qualified=%s",cycle,total,scanned,len(q))
                    if q:best=q[0]
                    time.sleep(SCAN)
                if best:
                    q,_,_=self._scan(candles,feed)
                    if q:best=q[0]
                    f=self._forming(feed,best["asset"])
                    if f and f.get("close") is not None:best["entry"]=float(f["close"]);best["candle"]=dict(f)
                    best["signal_time"]=time.time();best["target_time"]=target;best["sent_at"]=best["signal_time"];key=f"{cycle}|{best['asset']}|{best['direction']}|{best['expiry']}"
                    self.pending[key]=best;self.sent_cycles.add(cycle);self.stats["cycles"]+=1;self.stats["signals"]+=1;send(self._message(best,feed,target));LOG.info("CANDICE_SIGNAL cycle=%s asset=%s direction=%s expiry=%sm strategy=%s pattern=%s confidence=%s entry=%s",cycle,best['asset'],best['direction'],best['expiry'],best['strategy'],best['pattern'],best['confidence'],best['entry'])
                else:
                    self.sent_cycles.add(cycle);self.stats["cycles"]+=1;LOG.warning("CANDICE_CYCLE_NO_VALID_SETUP cycle=%s",cycle)
            except Exception:LOG.exception("brain loop error");time.sleep(2)
    def status(self):return {**self.stats,"pending":sum(1 for x in self.pending.values() if not x.get('result')),"expiries":list(EXPIRIES)}
    @staticmethod
    def telegram_send(text,chat):
        token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
        if not token or not chat:return False
        try:
            r=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",json={"chat_id":chat,"text":text},timeout=15);return r.ok
        except Exception:return False

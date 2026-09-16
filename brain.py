from __future__ import annotations
import json,logging,math,os,time
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
        try:return json.loads(self.learning_path.read_text())
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
        if l==0:return 100.0
        return 100-100/(1+g/l)
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
    def _trend(self,d):
        if len(d)<30:return 0,0
        c=[x["close"] for x in d];e9=self._ema(c,9);e21=self._ema(c,21);e50=self._ema(c,50);score=0
        if e9 and e21:score+=2 if e9>e21 else -2
        if e21 and e50:score+=2 if e21>e50 else -2
        adx=self._adx(d)
        if adx and adx>=18:score+=1 if d[-1]["close"]>d[-10]["close"] else -1
        return (1 if score>0 else -1 if score<0 else 0),score
    def _history_rate(self,key):
        x=self.learning["setups"].get(key,{"n":0,"w":0,"l":0,"t":0});n=x["n"]
        return (x["w"]+.5*x["t"])/n if n else .5
    def analyze(self,asset,completed,live_price):
        if len(completed)<60 or live_price is None:return None
        d=list(completed[-60:]);last=dict(d[-1]);last["close"]=live_price;data=d[:-1]+[last];cl=[x["close"] for x in data]
        t1,s1=self._trend(data[-30:]);m15=self._aggregate(d,15);t15,s15=self._trend(m15) if len(m15)>=3 else (0,0);rsi=self._rsi(cl);adx=self._adx(data);atr=self._atr(data);macd=self._macd(cl);pat,pdir=self._pattern(data)
        support=min(x["low"] for x in data[-30:]);res=max(x["high"] for x in data[-30:]);up=down=0;votes=[]
        def vote(name,direction,weight):
            nonlocal up,down
            if direction>0:up+=weight;votes.append(name+" UP")
            elif direction<0:down+=weight;votes.append(name+" DOWN")
        vote("Trend Following",1 if t1>0 and self._ema(cl,9)>self._ema(cl,21) else -1 if t1<0 else 0,3)
        vote("Breakout",1 if live_price>=max(x["high"] for x in data[-20:]) else -1 if live_price<=min(x["low"] for x in data[-20:]) else 0,3)
        vote("Pullback",1 if t15>0 and live_price<=self._ema(cl,21) and live_price>data[-2]["close"] else -1 if t15<0 and live_price>=self._ema(cl,21) and live_price<data[-2]["close"] else 0,3)
        vote("Support / Resistance",1 if atr and live_price-support<atr*.5 else -1 if atr and res-live_price<atr*.5 else 0,2)
        vote("Candlestick",pdir,3)
        vote("Momentum",1 if macd and macd[2]>0 else -1 if macd and macd[2]<0 else 0,3)
        vote("Mean Reversion",1 if rsi is not None and rsi<25 else -1 if rsi is not None and rsi>75 else 0,2)
        vote("Reversal",pdir if rsi is not None and ((pdir>0 and rsi<45) or (pdir<0 and rsi>55)) else 0,2)
        vote("Multi-Timeframe",t15,4);vote("Volatility",t15 if atr else 0,1);vote("Market Structure",t15,4)
        vote("Price Action",1 if live_price>data[-2]["close"] and last["close"]>last["open"] else -1 if live_price<data[-2]["close"] and last["close"]<last["open"] else 0,2)
        direction=1 if up>down else -1 if down>up else 0
        if not direction or up+down<10:return None
        if t15 and direction!=t15 and abs(s15)>=3:return None
        dname="UP" if direction>0 else "DOWN";strategy=max(STRATEGIES,key=lambda s:sum(1 for v in votes if v.startswith(s)))
        # Use learned outcome rates to rank strategy/expiry, with unseen combinations neutral.
        ranked=[]
        for strategy in STRATEGIES:
            for expiry in EXPIRIES:
                key=f"{asset}|{dname}|{pat}|{strategy}|{t15}|{expiry}";rate=self._history_rate(key);fit=(3 if strategy in votes and any(v.startswith(strategy) and v.endswith(dname) for v in votes) else 0)
                ranked.append((rate,fit,-abs(expiry-5),strategy,expiry))
        ranked.sort(reverse=True);_,_,_,strategy,expiry=ranked[0]
        confidence=int(max(58,min(96,58+(up+down)*1.15+min(10,abs(s15))*1.2+(self._history_rate(f"{asset}|{dname}|{pat}|{strategy}|{t15}|{expiry}")-.5)*12)))
        if confidence<58:return None
        return {"asset":asset,"direction":dname,"confidence":confidence,"pattern":pat,"strategy":strategy,"expiry":expiry,"trend_15":"UP" if t15>0 else "DOWN" if t15<0 else "FLAT","structure_1m":"BULLISH" if t1>0 else "BEARISH" if t1<0 else "NEUTRAL","rsi":rsi or 0,"adx":adx or 0,"entry":live_price,"candle":last,"previous":d[-2],"votes":votes,"created":time.time()}
    def _message(self,s,target):
        st=self.feed.status();now=time.time();local=datetime.now(timezone.utc)+timedelta(hours=4);tar=datetime.fromtimestamp(target,timezone.utc)+timedelta(hours=4);bal=st.get("account_balance");b="NOT_AVAILABLE" if bal is None else f"{bal:.2f} {st.get('account_currency','')}".strip()
        return f"━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI\n━━━━━━━━━━━━━━━━━━━━\n📊 ASSET: {s['asset']}\n➡️ DIRECTION: {s['direction']}\n\n🕒 SIGNAL: {local.strftime('%H:%M:%S')} UAE\n🎯 TARGET: {tar.strftime('%H:%M:%S')} UAE\n⏳ SIGNAL COUNTDOWN: {max(0,int(target-now)):02d}\n\n⏱️ EXPIRY: {s['expiry']} MIN\n💰 ENTRY: {s['entry']:.6f}\n📈 15M TREND: {s['trend_15']}\n🕯️ 1M STRUCTURE: {s['structure_1m']}\n🧠 STRATEGY: {s['strategy']}\n🔎 PATTERN: {s['pattern']}\n📊 RSI: {s['rsi']:.1f} | ADX: {s['adx']:.1f}\n🧠 CONFIDENCE: {s['confidence']}%\n\n🟢 ACCOUNT: {st.get('account_mode','UNKNOWN')}\n💳 BALANCE: {b}\n\n🔐 READ-ONLY / MANUAL ONLY\n🚫 AUTO-TRADE: OFF\n🚫 MARTINGALE: OFF\n━━━━━━━━━━━━━━━━━━━━"
    def _result(self,s):
        if s.get("result") or time.time()<s["signal_time"]+s["expiry"]*60:return
        price=self.feed.live_price(s["asset"])
        if price is None:return
        diff=price-s["entry"];result="TIE" if abs(diff)<=max(abs(s["entry"])*1e-8,1e-10) else "WIN" if (diff>0)==(s["direction"]=="UP") else "LOSS";s["exit"]=price;s["result"]=result;s["result_time"]=time.time()
        key=f"{s['asset']}|{s['direction']}|{s['pattern']}|{s['strategy']}|{s['trend_15']}|{s['expiry']}";x=self.learning["setups"].setdefault(key,{"n":0,"w":0,"l":0,"t":0});x["n"]+=1;x[{"WIN":"w","LOSS":"l","TIE":"t"}[result]]+=1;self._save();self.stats[result]+=1
        st=self.feed.status();bal=st.get("account_balance");b="NOT_AVAILABLE" if bal is None else f"{bal:.2f} {st.get('account_currency','')}".strip()
        self.send(f"━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI RESULT\n━━━━━━━━━━━━━━━━━━━━\n📊 ASSET: {s['asset']}\n➡️ DIRECTION: {s['direction']}\n💰 ENTRY: {s['entry']:.6f}\n💰 EXIT: {price:.6f}\n⏱️ EXPIRY: {s['expiry']} MIN\n🏁 RESULT: {result}\n🕒 ENTRY: {datetime.fromtimestamp(s['signal_time'],timezone.utc).strftime('%H:%M:%S')} UAE\n🏁 RESULT: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UAE\n🧠 STRATEGY: {s['strategy']}\n📈 15M TREND: {s['trend_15']}\n💳 BALANCE: {b}\n🟢 ACCOUNT: {st.get('account_mode','UNKNOWN')}\n🧠 LEARNING: UPDATED\n━━━━━━━━━━━━━━━━━━━━")
        log.info("CANDICE_RESULT asset=%s result=%s entry=%s exit=%s expiry=%sm",s["asset"],result,s["entry"],price,s["expiry"])
    def on_candle(self,asset,candle):
        for s in list(self.pending.values()):
            if s["asset"]==asset:self._result(s)
        log.info("REAL_1M_RESEARCH asset=%s candle=%s close=%s",asset,int(candle["timestamp"]),candle["close"])
    def run(self):
        log.info("CANDICE_BRAIN_ON | ALL_ASSETS | LIVE_PRICE + LIVE_CANDLE | 1m + 1h + 15m | 5m cycles")
        while True:
            now=time.time();target=(int(now)//300+1)*300;start=target-40;cycle=int(target//300)
            for s in list(self.pending.values()):self._result(s)
            if cycle in self.sent_cycles:time.sleep(1);continue
            if now<start:time.sleep(min(1,start-now));continue
            best=None
            # Continuous 40-second live decision window. No frozen snapshot.
            while time.time()<target:
                candidates=[];assets=self.feed.status().get("assets",[])
                for asset in assets:
                    c=self.feed.snapshot(asset);p=self.feed.live_price(asset)
                    try:
                        x=self.analyze(asset,c,p)
                        if x:candidates.append(x)
                    except Exception:log.exception("ASSET_ANALYSIS_FAILED asset=%s",asset)
                candidates.sort(key=lambda x:(x["confidence"],-x["expiry"]),reverse=True)
                if candidates:best=candidates[0]
                log.info("CANDICE_LIVE_SCAN cycle=%s assets=%s qualified=%s",cycle,len(assets),len(candidates))
                time.sleep(.5)
            self.sent_cycles.add(cycle)
            if best:
                # Fresh entry is taken at the send instant, not from the earlier scan.
                fresh=self.feed.live_price(best["asset"])
                if fresh is None:continue
                best["entry"]=fresh;best["signal_time"]=time.time();best["target_time"]=target;best["sent_at"]=best["signal_time"];self.pending[f"{cycle}|{best['asset']}"]=best;self.send(self._message(best,target));log.info("CANDICE_SIGNAL cycle=%s asset=%s direction=%s expiry=%sm entry=%s",cycle,best['asset'],best['direction'],best['expiry'],fresh)
            else:log.warning("CANDICE_NO_VALID_SETUP cycle=%s | continued to next live cycle",cycle)

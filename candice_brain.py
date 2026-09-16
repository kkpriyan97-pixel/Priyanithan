from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable

import requests

LOG = logging.getLogger("candice.brain")
EXPIRIES = (1, 2, 3, 4, 5, 10, 15)
CYCLE_SECONDS = 300
SIGNAL_LEAD_SECONDS = 40
SCAN_SECONDS = 2
HISTORY_MIN = 60
LEARNING_FILE = Path(os.getenv("CANDICE_LEARNING_FILE", "candice_learning.json"))


class CandiceBrain:
    """Clean implementation of the user's manual Candice workflow.

    Read-only: it never sends a broker order and never selects a broker account.
    It reads the live feed, builds 1m frames, studies 1h + 15m structure,
    identifies patterns, tests strategy/expiry combinations, ranks all live
    assets, and sends one manual signal per 5-minute decision cycle when a
    valid setup exists.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.sent_cycles = set()
        self.pending = {}
        self.learning = self._load_learning()
        self.stats = {"cycles": 0, "signals": 0, "wins": 0, "losses": 0, "ties": 0, "scans": 0}

    # ---------- persistence / learning ----------
    def _load_learning(self):
        try:
            if LEARNING_FILE.exists():
                return json.loads(LEARNING_FILE.read_text(encoding="utf-8"))
        except Exception:
            LOG.exception("Learning file could not be loaded")
        return {"combos": {}, "patterns": {}, "assets": {}}

    def _save_learning(self):
        try:
            LEARNING_FILE.write_text(json.dumps(self.learning, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            LOG.warning("Learning save failed: %s", type(e).__name__)

    @staticmethod
    def _bucket(d: dict, key: str):
        x = d.setdefault(key, {"n": 0, "w": 0, "l": 0, "t": 0})
        return x

    def _learn_result(self, signal: dict, result: str):
        combo_key = f"{signal['asset']}|{signal['strategy']}|{signal['pattern']}|{signal['expiry']}|{signal['direction']}"
        pattern_key = f"{signal['pattern']}|{signal['strategy']}|{signal['expiry']}|{signal['direction']}"
        asset_key = signal["asset"]
        for group, key in ((self.learning["combos"], combo_key), (self.learning["patterns"], pattern_key), (self.learning["assets"], asset_key)):
            x = self._bucket(group, key); x["n"] += 1
            if result == "WIN": x["w"] += 1
            elif result == "LOSS": x["l"] += 1
            else: x["t"] += 1
        with self.lock:
            self.stats["wins"] += result == "WIN"
            self.stats["losses"] += result == "LOSS"
            self.stats["ties"] += result == "TIE"
        self._save_learning()

    @staticmethod
    def _rate(x):
        n = int(x.get("n", 0))
        return (int(x.get("w", 0)) + 0.5 * int(x.get("t", 0))) / n if n else 0.5

    # ---------- candle math ----------
    @staticmethod
    def _closes(data): return [float(x["close"]) for x in data]

    @staticmethod
    def _ema(v, p):
        if len(v) < p: return None
        k = 2 / (p + 1); z = sum(v[:p]) / p
        for x in v[p:]: z = x * k + z * (1 - k)
        return z

    @staticmethod
    def _rsi(v, p=14):
        if len(v) <= p: return None
        gains = []; losses = []
        for a, b in zip(v[-p-1:-1], v[-p:]):
            d = b - a; gains.append(max(d, 0)); losses.append(max(-d, 0))
        g = sum(gains) / p; l = sum(losses) / p
        return 100.0 if l == 0 else 100.0 - 100.0 / (1.0 + g / l)

    @staticmethod
    def _atr(data, p=14):
        if len(data) <= p: return None
        trs = []
        for a, b in zip(data[-p-1:-1], data[-p:]):
            trs.append(max(b["high"]-b["low"], abs(b["high"]-a["close"]), abs(b["low"]-a["close"])))
        return sum(trs) / p

    @staticmethod
    def _adx(data, p=14):
        if len(data) < p * 2 + 2: return None, None, None
        tr=[]; plus=[]; minus=[]
        for a,b in zip(data[1:],data[2:]):
            up=b["high"]-a["high"]; dn=a["low"]-b["low"]
            tr.append(max(b["high"]-b["low"],abs(b["high"]-a["close"]),abs(b["low"]-a["close"])))
            plus.append(up if up>dn and up>0 else 0); minus.append(dn if dn>up and dn>0 else 0)
        dx=[]; di=[]
        for i in range(p,len(tr)+1):
            tv=sum(tr[i-p:i]); pi=100*sum(plus[i-p:i])/tv if tv else 0; mi=100*sum(minus[i-p:i])/tv if tv else 0
            di.append((pi,mi)); dx.append(100*abs(pi-mi)/(pi+mi) if pi+mi else 0)
        if len(dx)<p:return None,None,None
        return sum(dx[-p:])/p,di[-1][0],di[-1][1]

    @staticmethod
    def _macd(v):
        if len(v)<40:return None,None,None
        line=[]
        for i in range(26,len(v)+1):
            a=CandiceBrain._ema(v[:i],12); b=CandiceBrain._ema(v[:i],26)
            if a is not None and b is not None:line.append(a-b)
        sig=CandiceBrain._ema(line,9) if len(line)>=9 else None
        return line[-1],sig,(line[-1]-sig if sig is not None else None)

    @staticmethod
    def _stoch(data,p=14):
        if len(data)<p:return None
        w=data[-p:]; hi=max(x["high"] for x in w); lo=min(x["low"] for x in w)
        return 50 if hi==lo else 100*(w[-1]["close"]-lo)/(hi-lo)

    @staticmethod
    def _aggregate(data, n):
        out=[]
        for i in range(0,len(data)-n+1,n):
            w=data[i:i+n]
            out.append({"open":w[0]["open"],"high":max(x["high"] for x in w),"low":min(x["low"] for x in w),"close":w[-1]["close"],"timestamp":w[-1]["timestamp"]})
        return out

    def _trend(self, data):
        if len(data)<20:return 0,0
        c=self._closes(data); e9=self._ema(c,9); e21=self._ema(c,21); e50=self._ema(c,50)
        score=0
        if e9 and e21: score += 2 if e9>e21 else -2
        if e21 and e50: score += 2 if e21>e50 else -2
        adx,dip,dim=self._adx(data)
        if adx and adx>=18: score += 2 if dip>dim else -2
        return (1 if score>0 else -1 if score<0 else 0), score

    # ---------- pattern / strategy analysis ----------
    def _pattern(self, data):
        if len(data)<3:return "neutral",0
        a,b=data[-2],data[-1]
        body=abs(b["close"]-b["open"]); rng=max(b["high"]-b["low"],1e-12)
        upper=b["high"]-max(b["open"],b["close"]); lower=min(b["open"],b["close"])-b["low"]
        if body/rng<.25 and lower/rng>.55:return "hammer",1
        if body/rng<.25 and upper/rng>.55:return "shooting_star",-1
        if b["close"]>b["open"] and a["close"]<a["open"] and b["close"]>=a["open"] and b["open"]<=a["close"]:return "bullish_engulfing",1
        if b["close"]<b["open"] and a["close"]>a["open"] and b["open"]>=a["close"] and b["close"]<=a["open"]:return "bearish_engulfing",-1
        if b["close"]>b["open"] and b["close"]>a["close"]:return "bull_continuation",1
        if b["close"]<b["open"] and b["close"]<a["close"]:return "bear_continuation",-1
        return "neutral",0

    def _pattern_frequency(self, hour):
        counts=defaultdict(int)
        for i in range(2,len(hour)):
            p,_=self._pattern(hour[:i+1]); counts[p]+=1
        return dict(sorted(counts.items(),key=lambda x:x[1],reverse=True))

    def _strategy_votes(self, data, trend15, pattern_dir):
        c=self._closes(data); last=data[-1]; e9=self._ema(c,9); e21=self._ema(c,21); e50=self._ema(c,50); r=self._rsi(c); m,ms,mh=self._macd(c); adx,dip,dim=self._adx(data); atr=self._atr(data)
        up=down=0; reasons=[]
        def vote(name, d, strength):
            nonlocal up,down
            (up if d>0 else down).__class__
            if d>0: up += strength
            elif d<0: down += strength
            if d: reasons.append(f"{name}={'UP' if d>0 else 'DOWN'}")
        # Trend following
        vote("trend_following", 1 if e9 and e21 and e9>e21 and (not e50 or e21>e50) else -1 if e9 and e21 and e9<e21 and (not e50 or e21<e50) else 0, 3)
        # Breakout
        hi=max(x["high"] for x in data[-20:]); lo=min(x["low"] for x in data[-20:]); vote("breakout",1 if last["close"]>=hi else -1 if last["close"]<=lo else 0,3)
        # Pullback
        pull=1 if trend15>0 and e21 and last["low"]<=e21 and last["close"]>e21 else -1 if trend15<0 and e21 and last["high"]>=e21 and last["close"]<e21 else 0; vote("pullback",pull,3)
        # S/R
        support=min(x["low"] for x in data[-30:]); resistance=max(x["high"] for x in data[-30:]); vote("support_resistance",1 if atr and last["close"]-support<atr*.5 else -1 if atr and resistance-last["close"]<atr*.5 else 0,2)
        # Candle
        vote("candlestick",pattern_dir,3)
        # Momentum
        vote("momentum",1 if m is not None and ms is not None and m>ms and mh>0 else -1 if m is not None and ms is not None and m<ms and mh<0 else 0,3)
        # Mean reversion
        vote("mean_reversion",1 if r is not None and r<25 else -1 if r is not None and r>75 else 0,2)
        # Reversal
        vote("reversal",pattern_dir if r is not None and ((pattern_dir>0 and r<45) or (pattern_dir<0 and r>55)) else 0,2)
        # Multi-timeframe
        vote("multi_timeframe",trend15,4)
        # Volatility
        vote("volatility",trend15 if atr and atr>0 else 0,1)
        # Market structure
        vote("market_structure",trend15,4)
        # Price action
        vote("price_action",1 if last["close"]>last["open"] and last["close"]>=data[-2]["close"] else -1 if last["close"]<last["open"] and last["close"]<=data[-2]["close"] else 0,2)
        return up,down,reasons

    def _expiry_scores(self, asset, strategy, pattern, direction):
        scores={}
        for e in EXPIRIES:
            key=f"{asset}|{strategy}|{pattern}|{e}|{direction}"
            scores[e]=self._rate(self.learning["combos"].get(key,{"n":0,"w":0,"l":0,"t":0}))
        return scores

    def analyze_asset(self, asset: str, data: list[dict], forming: dict | None):
        if len(data)<HISTORY_MIN:return None
        hour=data[-60:]
        live=list(hour)
        if forming and float(forming.get("timestamp",0))>=float(live[-1]["timestamp"]):
            live.append(dict(forming))
        c=self._closes(live); r=self._rsi(c); atr=self._atr(live); adx,dip,dim=self._adx(live); m,ms,mh=self._macd(c)
        trend1,score1=self._trend(live[-30:]); trend15,score15=self._trend(self._aggregate(hour,15));
        pattern,pattern_dir=self._pattern(live); freq=self._pattern_frequency(hour)
        up,down,reasons=self._strategy_votes(live,trend15,pattern_dir)
        direction=1 if up>down else -1 if down>up else 0
        agreement=up+down
        if direction==0 or agreement<9:return None
        # 15m confirmation is a final gate, but strong 1m reversal can be considered only when 15m is neutral.
        if trend15 and direction!=trend15 and abs(score15)>=4:return None
        if trend1 and direction!=trend1 and abs(score1)>=6:return None
        strategy="market_structure"
        candidates={"trend_following":3,"breakout":3,"pullback":3,"support_resistance":2,"candlestick":3,"momentum":3,"mean_reversion":2,"reversal":2,"multi_timeframe":4,"volatility":1,"market_structure":4,"price_action":2}
        # Pick the strategy most aligned with the final direction, adjusted by learned history.
        best=(-1,None)
        for name,w in candidates.items():
            hist=self.learning["patterns"].get(f"{pattern}|{name}|5|{'UP' if direction>0 else 'DOWN'}",{})
            val=w*(0.5+self._rate(hist))
            if val>best[0]:best=(val,name)
        strategy=best[1] or "market_structure"
        exp_scores=self._expiry_scores(asset,strategy,pattern,"UP" if direction>0 else "DOWN")
        expiry=max(EXPIRIES,key=lambda e:(exp_scores[e], -abs(e-5)))
        # Unseen combinations start at 50%; do not claim they are historically proven.
        history_bonus=(exp_scores[expiry]-0.5)*20
        confidence=max(55,min(96,int(55+min(30,agreement*1.2)+min(8,abs(score15))+history_bonus)))
        if confidence<58:return None
        pattern_count=freq.get(pattern,0)
        return {"asset":asset,"direction":"UP" if direction>0 else "DOWN","direction_num":direction,"confidence":confidence,"strategy":strategy,"pattern":pattern,"pattern_frequency":pattern_count,"expiry":expiry,"trend_15":"UP" if trend15>0 else "DOWN" if trend15<0 else "FLAT","trend_15_score":score15,"trend_1":"UP" if trend1>0 else "DOWN" if trend1<0 else "FLAT","trend_1_score":score1,"rsi":r,"adx":adx,"di_plus":dip,"di_minus":dim,"atr":atr,"entry":float(live[-1]["close"]),"candle":dict(live[-1]),"previous_candle":dict(live[-2]),"reasons":reasons[-8:],"expiry_history":exp_scores,"hour_pattern_frequency":freq,"created":time.time()}

    # ---------- live loop ----------
    @staticmethod
    def _forming(feed, asset):
        try:return dict((feed.forming or {}).get(asset) or {}) or None
        except Exception:return None

    def _scan_all(self, candles, feed):
        assets=[]
        try: assets=list((feed.status() or {}).get("assets") or [])
        except Exception: assets=[]
        candidates=[]; scanned=0
        for asset in assets:
            with threading.RLock():
                data=list(candles.get(asset, []))
            forming=self._forming(feed,asset)
            if len(data)<HISTORY_MIN:continue
            scanned+=1
            try:
                result=self.analyze_asset(asset,data,forming)
                if result:candidates.append(result)
            except Exception:
                LOG.exception("Analysis failed asset=%s",asset)
        self.stats["scans"]+=scanned
        # Strongest live setup; recent asset repetition gets a small penalty, not a forced rotation.
        recent={v["asset"] for v in self.pending.values() if time.time()-v.get("sent_at",0)<1800}
        for x in candidates:
            x["rank"] = x["confidence"] - (3 if x["asset"] in recent else 0)
        candidates.sort(key=lambda x:(x["rank"],x["confidence"],x["created"]),reverse=True)
        return candidates, len(assets), scanned

    def _signal_message(self, s, feed, target_ts):
        status=feed.status() if feed else {}
        mode=status.get("account_mode","UNKNOWN"); bal=status.get("account_balance")
        currency=status.get("account_currency","")
        balance="NOT_AVAILABLE" if bal is None else f"{float(bal):.2f} {currency}".strip()
        target=datetime.fromtimestamp(target_ts,timezone.utc)+timedelta(hours=4)
        sent=datetime.now(timezone.utc)+timedelta(hours=4)
        return ("━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • LIVE MARKET\n━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 ASSET • {s['asset']}\n➡️ DIRECTION • {s['direction']}\n"
                f"💰 ENTRY • {s['entry']:.6f}\n⏱️ EXPIRY • {s['expiry']} MIN\n"
                f"🕒 SIGNAL • {sent.strftime('%H:%M:%S')} UAE\n🎯 TARGET • {target.strftime('%H:%M:%S')} UAE\n"
                f"⏳ COUNTDOWN • {max(0,int(target_ts-time.time())):02d}s\n"
                f"📈 15M TREND • {s['trend_15']}\n🕯️ 1M STRUCTURE • {s['trend_1']}\n"
                f"🧠 STRATEGY • {s['strategy']}\n🔎 PATTERN • {s['pattern']} ({s['pattern_frequency']} in last hour)\n"
                f"📊 RSI • {s['rsi']:.1f} | ADX • {s['adx']:.1f}\n🧠 CONFIDENCE • {s['confidence']}%\n"
                f"💳 ACCOUNT • {mode}\n💰 BALANCE • {balance}\n"
                "🔐 MODE • READ-ONLY / MANUAL ONLY\n🚫 AUTO-TRADE • OFF\n🚫 MARTINGALE • OFF\n"
                "━━━━━━━━━━━━━━━━━━━━")

    def _result_check(self, signal, candles, feed, send):
        now=time.time(); expiry_at=signal["signal_time"]+signal["expiry"]*60
        if now<expiry_at:return False
        if signal.get("result"):
            return True
        price=None
        forming=self._forming(feed,signal["asset"])
        if forming:price=float(forming.get("close",0) or 0)
        if not price:
            data=list(candles.get(signal["asset"],[])); price=float(data[-1]["close"]) if data else None
        if price is None:return False
        entry=signal["entry"]; direction=1 if signal["direction"]=="UP" else -1
        eps=max(abs(entry)*1e-8,1e-10)
        result="TIE" if abs(price-entry)<=eps else "WIN" if (price-entry)*direction>0 else "LOSS"
        signal["exit"]=price;signal["result"]=result;signal["result_time"]=now
        self._learn_result(signal,result)
        self.stats["signals"] = self.stats.get("signals",0)
        msg=("━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • RESULT\n━━━━━━━━━━━━━━━━━━━━\n"
             f"📊 ASSET • {signal['asset']}\n➡️ DIRECTION • {signal['direction']}\n"
             f"💰 ENTRY • {entry:.6f}\n💰 EXIT • {price:.6f}\n⏱️ EXPIRY • {signal['expiry']} MIN\n"
             f"🏁 RESULT • {result}\n🧠 STRATEGY • {signal['strategy']}\n🔎 PATTERN • {signal['pattern']}\n"
             f"📈 15M TREND • {signal['trend_15']}\n💳 ACCOUNT • {(feed.status() or {}).get('account_mode','UNKNOWN')}\n"
             f"💰 BALANCE • {(feed.status() or {}).get('account_balance','NOT_AVAILABLE')}\n"
             "🧠 LEARNING • UPDATED\n━━━━━━━━━━━━━━━━━━━━")
        send(msg)
        LOG.info("CANDICE_RESULT asset=%s result=%s entry=%s exit=%s expiry=%sm",signal["asset"],result,entry,price,signal["expiry"])
        return True

    def on_completed_candle(self, asset, candle, candles, feed, send):
        # Results are checked independently of the 5-minute signal cycle.
        for sig in list(self.pending.values()):
            if sig["asset"]==asset and not sig.get("result"):
                self._result_check(sig,candles,feed,send)
        LOG.info("REAL_1M_RESEARCH asset=%s candle_ts=%s close=%s",asset,int(candle["timestamp"]),candle["close"])

    def loop(self, candles, feed_getter: Callable, send: Callable):
        LOG.info("CANDICE_BRAIN started | LIVE DIRECT VIEW | all available assets | 1m + 1h + 15m | cycles=5m | lead=40s | Dubai")
        while True:
            try:
                feed=feed_getter()
                if not feed:
                    time.sleep(1); continue
                now=time.time(); cycle=int(now//CYCLE_SECONDS); target=(cycle+1)*CYCLE_SECONDS; start=target-SIGNAL_LEAD_SECONDS
                self._result_pending(candles,feed,send)
                if cycle in self.sent_cycles:
                    time.sleep(1); continue
                if now < start:
                    time.sleep(min(1.0,start-now)); continue
                self.stats["cycles"]+=1
                best=None
                deadline=target
                while time.time()<deadline and cycle not in self.sent_cycles:
                    candidates,total,scanned=self._scan_all(candles,feed)
                    LOG.info("CANDICE LIVE SCAN cycle=%s | assets=%s | scanned=%s | qualified=%s | target=%s",cycle,total,scanned,len(candidates),datetime.fromtimestamp(target,timezone.utc).strftime('%H:%M:%S'))
                    if candidates:
                        best=candidates[0]
                        # Re-check continuously; latest live price/structure wins.
                        if time.time()>=target-2:break
                    time.sleep(SCAN_SECONDS)
                if best and cycle not in self.sent_cycles:
                    # Final direct-live read immediately before sending.
                    fresh=self._scan_all(candles,feed)[0]
                    if fresh:best=fresh[0]
                    signal_time=time.time(); forming=self._forming(feed,best["asset"])
                    if forming and forming.get("close") is not None:best["entry"]=float(forming["close"]);best["candle"]=dict(forming)
                    best["signal_time"]=signal_time;best["target_time"]=target;best["sent_at"]=signal_time
                    key=f"{cycle}|{best['asset']}|{best['direction']}|{best['expiry']}"
                    if key not in self.sent_cycles:
                        self.sent_cycles.add(cycle);self.pending[key]=best;self.stats["signals"]+=1
                        send(self._signal_message(best,feed,target))
                        LOG.info("CANDICE_SIGNAL cycle=%s asset=%s direction=%s expiry=%sm strategy=%s pattern=%s confidence=%s entry=%s",cycle,best["asset"],best["direction"],best["expiry"],best["strategy"],best["pattern"],best["confidence"],best["entry"])
                else:
                    self.sent_cycles.add(cycle)
                    LOG.warning("CANDICE_CYCLE_NO_VALID_SETUP cycle=%s | live scan completed without valid 15m-confirmed setup",cycle)
            except Exception:
                LOG.exception("Candice brain loop error")
                time.sleep(2)

    def _result_pending(self,candles,feed,send):
        for sig in list(self.pending.values()):
            if not sig.get("result"):
                self._result_check(sig,candles,feed,send)

    def status(self):
        with self.lock:
            return {"cycles":self.stats["cycles"],"signals":self.stats["signals"],"wins":self.stats["wins"],"losses":self.stats["losses"],"ties":self.stats["ties"],"pending":sum(1 for x in self.pending.values() if not x.get("result")),"expiries":list(EXPIRIES)}

    @staticmethod
    def telegram_send(text, chat_id):
        token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
        if not token or not chat_id:
            LOG.warning("Telegram delivery skipped: token/chat not configured")
            return False
        try:
            r=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",json={"chat_id":chat_id,"text":text,"disable_web_page_preview":True},timeout=15)
            if r.ok:return True
            LOG.warning("Telegram delivery failed HTTP=%s",r.status_code);return False
        except Exception as e:
            LOG.warning("Telegram delivery error=%s",type(e).__name__);return False

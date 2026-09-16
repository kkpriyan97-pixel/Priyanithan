from __future__ import annotations
import math
import time
import json
from datetime import datetime, timezone, timedelta
from brain import CandiceBrain

UAE = timezone(timedelta(hours=4))


def _trend15(self, completed):
    if len(completed) < 15:
        return 0, 0
    d = list(completed[-15:])
    closes = [float(x['close']) for x in d]
    e5 = self._ema(closes, 5); e9 = self._ema(closes, 9); score = 0
    if e5 is not None and e9 is not None: score += 2 if e5 > e9 else -2 if e5 < e9 else 0
    score += 1 if closes[-1] > closes[0] else -1 if closes[-1] < closes[0] else 0
    return (1 if score > 0 else -1 if score < 0 else 0), score


def analyze(self, asset, completed, live_price):
    if getattr(self.feed, 'client', None) is None or self.feed.client.account_mode != 'DEMO': return None
    if asset not in set(self.feed.status().get('assets', [])) or asset not in set(self.feed.status().get('assets', [])): return None
    if len(completed) < 60 or live_price is None: return None
    d = list(completed[-60:])
    latest_ts = float(d[-1].get('timestamp', 0)); now = time.time()
    if latest_ts <= 0 or now - latest_ts > 120: return None
    closes = [float(x['close']) for x in d]
    t1, s1 = self._trend(d[-30:], 5, 9); t15, s15 = _trend15(self, d)
    rsi = self._rsi(closes); adx = self._adx(d); atr = self._atr(d); macd = self._macd(closes); pat, pdir = self._pattern(d)
    e5, e9 = self._ema(closes, 5), self._ema(closes, 9); up = down = 0; votes = []
    def vote(name, direction, weight):
        nonlocal up, down
        if direction > 0: up += weight; votes.append(name + ' UP')
        elif direction < 0: down += weight; votes.append(name + ' DOWN')
    vote('Trend Following', 1 if e5 is not None and e9 is not None and e5 > e9 else -1 if e5 is not None and e9 is not None and e5 < e9 else 0, 3)
    hi = max(x['high'] for x in d[-20:-1]); lo = min(x['low'] for x in d[-20:-1])
    vote('Breakout', 1 if live_price > hi else -1 if live_price < lo else 0, 3)
    vote('Candlestick', pdir, 3); vote('Momentum', 1 if macd and macd[2] > 0 else -1 if macd and macd[2] < 0 else 0, 3)
    vote('Mean Reversion', 1 if rsi is not None and rsi < 25 else -1 if rsi is not None and rsi > 75 else 0, 2)
    vote('Multi-Timeframe', t15, 4); vote('Market Structure', t15, 4)
    vote('Price Action', 1 if live_price > d[-1]['close'] else -1 if live_price < d[-1]['close'] else 0, 2)
    if t15 and t1 and t15 != t1 and abs(s15) >= 3: return None
    direction = 1 if up > down else -1 if down > up else 0
    if not direction or up + down < 12: return None
    dname = 'UP' if direction > 0 else 'DOWN'; ranked=[]
    for strategy in ('Trend Following','Breakout','Pullback','Support / Resistance','Candlestick','Momentum','Mean Reversion','Reversal','Multi-Timeframe','Volatility','Market Structure','Price Action'):
        fit = 3 if any(v.startswith(strategy + ' ' + dname) for v in votes) else 0
        for expiry in (1,2,3,4,5,10,15):
            key=f'{asset}|{dname}|{pat}|{strategy}|{t15}|{expiry}'
            ranked.append((self._history_rate(key),fit,-abs(expiry-5),strategy,expiry,key))
    ranked.sort(reverse=True); _,_,_,strategy,expiry,key=ranked[0]
    confidence=int(max(58,min(96,58+(up+down)*1.15+min(10,abs(s15))*1.2+(self._history_rate(key)-.5)*12)))
    if adx is None or rsi is None or macd is None or atr is None: return None
    if confidence < 70: return None
    return {'asset':asset,'direction':dname,'confidence':confidence,'pattern':pat,'strategy':strategy,'expiry':expiry,'trend_15':'UP' if t15>0 else 'DOWN' if t15<0 else 'FLAT','structure_1m':'BULLISH' if t1>0 else 'BEARISH' if t1<0 else 'NEUTRAL','rsi':rsi,'adx':adx,'entry':float(live_price),'candle':d[-1],'previous':d[-2],'votes':votes,'created':time.time()}


def message(self, s, target, countdown=None):
    st=self.feed.status(); signal=datetime.fromtimestamp(float(s['signal_time']),timezone.utc).astimezone(UAE); target_local=datetime.fromtimestamp(float(target),timezone.utc).astimezone(UAE)
    if countdown is None: countdown=max(0,int(math.ceil(float(target)-time.time())))
    countdown=max(0,min(40,int(countdown))); bal=st.get('account_balance'); balance='NOT_AVAILABLE' if bal is None else f"{bal:.2f} {st.get('account_currency','')}".strip()
    return ('━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • LIVE MARKET\n━━━━━━━━━━━━━━━━━━━━\n'
            f"📊 ASSET: {s['asset']}\n➡️ DIRECTION: {s['direction']}\n\n🕒 SIGNAL: {signal.strftime('%H:%M:%S')} UAE\n🎯 TARGET: {target_local.strftime('%H:%M:%S')} UAE\n⏳ SIGNAL COUNTDOWN: {countdown:02d}\n\n⏱️ EXPIRY: {s['expiry']} MIN\n💰 ENTRY: {s['entry']:.6f}\n📈 15M TREND: {s['trend_15']}\n🕯️ 1M STRUCTURE: {s['structure_1m']}\n🧠 STRATEGY: {s['strategy']}\n🔎 PATTERN: {s['pattern']}\n📊 RSI: {s['rsi']:.1f} | ADX: {s['adx']:.1f}\n🧠 CONFIDENCE: {s['confidence']}%\n\n🟢 ACCOUNT: {st.get('account_mode','UNKNOWN')}\n💳 BALANCE: {balance}\n\n🔐 READ-ONLY / MANUAL ONLY\n🚫 AUTO-TRADE: OFF\n🚫 MARTINGALE: OFF\n━━━━━━━━━━━━━━━━━━━━')


def countdown(self,s,target):
    mid=s.get('telegram_message_id'); tg=getattr(self.send,'__self__',None)
    if not mid or tg is None:return
    last=None
    while time.time()<target and not s.get('result'):
        cd=max(0,min(40,int(math.ceil(target-time.time()))))
        if cd!=last:
            try:tg.edit(mid,message(self,s,target,cd))
            except Exception:pass
            last=cd
        time.sleep(.25)
    try:tg.edit(mid,message(self,s,target,0))
    except Exception:pass


def _record_result(self,s,outcome,price,result_time):
    key=f"{s['asset']}|{s['direction']}|{s['pattern']}|{s['strategy']}|{s['trend_15']}|{s['expiry']}"
    x=self.learning['setups'].setdefault(key,{'n':0,'w':0,'l':0,'t':0}); x['n']+=1; x[{'WIN':'w','LOSS':'l','TIE':'t'}[outcome]]+=1
    self._reset_day_if_needed(); self.stats[outcome]+=1
    if outcome=='LOSS': self.daily_losses+=1; self.consecutive_losses+=1
    elif outcome=='WIN': self.consecutive_losses=0
    day=self.learning['daily'].setdefault(self.day_key,{'losses':0,'consecutive_losses':0,'signals':0}); day['losses']=self.daily_losses; day['consecutive_losses']=self.consecutive_losses
    self._save()


def result(self,s):
    if s.get('result') or time.time()<s['expiry_start']: return True
    deadline=float(s['expiry_start'])+30; price=None
    while time.time()<=deadline and not s.get('result'):
        try: price=self.feed.live_price(s['asset'])
        except Exception: price=None
        if price is not None: break
        time.sleep(1)
    if price is None:
        log.warning('RESULT_DELAYED asset=%s reason=no_live_exit_price',s['asset']); return False
    diff=float(price)-float(s['entry']); tol=max(abs(float(s['entry']))*1e-8,1e-10)
    outcome='TIE' if abs(diff)<=tol else 'WIN' if ((diff>0)==(s['direction']=='UP')) else 'LOSS'
    s['result']=outcome; s['exit']=float(price); s['result_time']=time.time(); _record_result(self,s,outcome,float(price),s['result_time'])
    rt=datetime.fromtimestamp(s['result_time'],timezone.utc).astimezone(UAE); st=datetime.fromtimestamp(s['signal_time'],timezone.utc).astimezone(UAE); icon='🟢' if outcome=='WIN' else '🔴' if outcome=='LOSS' else '🟡'
    msg=('━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI RESULT\n━━━━━━━━━━━━━━━━━━━━\n'
         f"📊 ASSET: {s['asset']}\n➡️ DIRECTION: {s['direction']}\n\n💰 ENTRY: {s['entry']:.6f}\n💰 EXIT: {s['exit']:.6f}\n\n⏱️ EXPIRY: {s['expiry']} MIN\n{icon} RESULT: {outcome}\n\n🕒 SIGNAL: {st.strftime('%H:%M:%S')} UAE\n🏁 RESULT TIME: {rt.strftime('%H:%M:%S')} UAE\n🧠 STRATEGY: {s['strategy']}\n📈 15M TREND: {s['trend_15']}\n📚 DAILY LOSSES: {self.daily_losses}/{self.max_daily_losses}\n📚 CONSECUTIVE LOSSES: {self.consecutive_losses}/3\n🔐 DEMO / READ-ONLY\n━━━━━━━━━━━━━━━━━━━━')
    try:self.send(msg)
    except Exception as e:log.warning('RESULT_TELEGRAM_FAILED error=%s',type(e).__name__)
    log.info('RESULT asset=%s result=%s expiry=%s',s['asset'],outcome,s['expiry']); return True


def result_worker(self,s):
    wait=max(0,float(s['expiry_start'])-time.time())
    if wait:time.sleep(wait)
    attempts=0
    while not s.get('result'):
        attempts+=1
        try:
            if self._result(s):return
        except Exception as e:log.warning('RESULT_RETRY_FAILED attempt=%s error=%s',attempts,type(e).__name__)
        time.sleep(min(5,1+attempts*.25))


def apply():
    CandiceBrain.analyze=analyze; CandiceBrain._message=message; CandiceBrain._countdown=countdown; CandiceBrain._result=result; CandiceBrain._result_worker=result_worker

from __future__ import annotations
import json, logging, math, os, threading, time
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Any
import requests
from flask import Flask, jsonify, request
from olymp_live import OlympLiveFeed

VERSION='11.0-CANDICE-FULL-BRAIN-READONLY'
AUTO_TRADE=False
MARTINGALE=False
EXPIRIES=(2,3,5,10,15)
MIN_CONFIDENCE=max(50,min(95,int(os.getenv('MIN_CONFIDENCE','65'))))
MAX_DAILY_LOSSES=max(1,int(os.getenv('DAILY_MAX_LOSSES','5')))
MAX_CONSECUTIVE_LOSSES=max(1,int(os.getenv('MAX_CONSECUTIVE_LOSSES','3')))
TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','').strip(); CHAT_ID=os.getenv('TELEGRAM_CHAT_ID','').strip(); SECRET=os.getenv('TRADINGVIEW_WEBHOOK_SECRET','').strip()
logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log=logging.getLogger('candice'); app=Flask(__name__)
candles:dict[str,deque]= {}; sent=set(); last_webhook=0.0; telegram_offset=0
risk={'date':'','losses':0,'streak':0,'signals':0,'wins':0,'losses_total':0}
live_feed: OlympLiveFeed | None = None

def reset_risk():
    d=(datetime.now(timezone.utc)+timedelta(hours=4)).date().isoformat()
    if risk['date']!=d:risk.update(date=d,losses=0,streak=0,signals=0,wins=0,losses_total=0)

def f(v):
    try:return float(v)
    except:return None

def normalize(raw):
    out=[]
    if not isinstance(raw,list):return out
    for x in raw[-300:]:
        if not isinstance(x,dict):continue
        o=f(x.get('open',x.get('o'))); h=f(x.get('high',x.get('h'))); l=f(x.get('low',x.get('l'))); c=f(x.get('close',x.get('c'))); t=f(x.get('timestamp',x.get('time',x.get('t',time.time()))))
        if None in (o,h,l,c):continue
        if h<max(o,c) or l>min(o,c) or h<l:continue
        out.append({'open':o,'high':h,'low':l,'close':c,'timestamp':t or time.time()})
    return out

def ema(v,p):
    if len(v)<p:return None
    k=2/(p+1); z=sum(v[:p])/p
    for x in v[p:]:z=x*k+z*(1-k)
    return z

def sma(v,p):return sum(v[-p:])/p if len(v)>=p else None

def rsi(v,p=14):
    if len(v)<=p:return None
    d=[b-a for a,b in zip(v[-p-1:-1],v[-p:])]; g=sum(max(x,0) for x in d)/p; l=sum(max(-x,0) for x in d)/p
    return 100 if l==0 else 100-100/(1+g/l)

def atr(data,p=14):
    if len(data)<=p:return None
    z=[max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close'])) for a,b in zip(data[-p-1:-1],data[-p:])]
    return sum(z)/p

def macd(v):
    if len(v)<35:return None,None,None
    lines=[]
    for i in range(26,len(v)+1):
        a=ema(v[:i],12);b=ema(v[:i],26)
        if a is not None and b is not None:lines.append(a-b)
    sig=ema(lines,9) if len(lines)>=9 else None
    return lines[-1],sig,(lines[-1]-sig if sig is not None else None)

def stochastic(data,p=14,signal=3):
    if len(data)<p+signal:return None,None
    k=[]
    for i in range(p,len(data)+1):
        w=data[i-p:i];hi=max(x['high'] for x in w);lo=min(x['low'] for x in w);den=hi-lo
        k.append(50 if den==0 else 100*(w[-1]['close']-lo)/den)
    return k[-1],sma(k,signal)

def adx(data,p=14):
    if len(data)<p*2+1:return None,None,None
    tr=[];plus=[];minus=[]
    for a,b in zip(data[-(p*2+1):-1],data[-(p*2):]):
        up=b['high']-a['high'];dn=a['low']-b['low']
        tr.append(max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close'])))
        plus.append(up if up>dn and up>0 else 0);minus.append(dn if dn>up and dn>0 else 0)
    dx=[];dirs=[]
    for i in range(p,len(tr)+1):
        tv=sum(tr[i-p:i]);ps=100*sum(plus[i-p:i])/tv if tv else 0;ms=100*sum(minus[i-p:i])/tv if tv else 0
        dx.append(100*abs(ps-ms)/(ps+ms) if ps+ms else 0);dirs.append((ps,ms))
    if len(dx)<p:return None,None,None
    return sum(dx[-p:])/p,*dirs[-1]

def levels(data):
    w=data[-30:];return min(x['low'] for x in w),max(x['high'] for x in w)

def candle_pattern(last,prev):
    body=abs(last['close']-last['open']);rng=max(last['high']-last['low'],1e-12);upper=last['high']-max(last['open'],last['close']);lower=min(last['open'],last['close'])-last['low']
    if body/rng<.25 and lower/rng>.55:return 'hammer_bullish'
    if body/rng<.25 and upper/rng>.55:return 'shooting_star_bearish'
    if last['close']>last['open'] and prev['close']<prev['open'] and last['close']>=prev['open'] and last['open']<=prev['close']:return 'bullish_engulfing'
    if last['close']<last['open'] and prev['close']>prev['open'] and last['open']>=prev['close'] and last['close']<=prev['open']:return 'bearish_engulfing'
    return 'neutral'

def analyze(data):
    if len(data)<60:return {'decision':'NO_SIGNAL','confidence':0,'direction':None,'reasons':[f'Need 60+ one-minute candles ({len(data)}/60)']}
    c=[x['close'] for x in data];last=data[-1];prev=data[-2];e9,e21,e50=ema(c,9),ema(c,21),ema(c,50);rv= rsi(c);av=atr(data);ml,ms,md=macd(c);sk,ss=stochastic(data);ax,di_p,di_m=adx(data);support,resistance=levels(data)
    up=down=0;reasons=[];conflicts=0
    if e9 and e21:
        if e9>e21:up+=2;reasons.append('EMA9 > EMA21 bullish')
        else:down+=2;reasons.append('EMA9 < EMA21 bearish')
    if e21 and e50:
        if e21>e50:up+=2;reasons.append('EMA21 > EMA50 trend bullish')
        else:down+=2;reasons.append('EMA21 < EMA50 trend bearish')
    if rv is not None:
        if 52<=rv<=68:up+=1;reasons.append(f'RSI {rv:.1f} bullish zone')
        elif 32<=rv<=48:down+=1;reasons.append(f'RSI {rv:.1f} bearish zone')
        elif rv>=75:down+=1;conflicts+=1;reasons.append(f'RSI {rv:.1f} overbought caution')
        elif rv<=25:up+=1;conflicts+=1;reasons.append(f'RSI {rv:.1f} oversold caution')
    if md is not None and ms is not None:
        if md>0:up+=2;reasons.append('MACD histogram positive')
        else:down+=2;reasons.append('MACD histogram negative')
        if ml>ms:up+=1;reasons.append('MACD line above signal')
        else:down+=1;reasons.append('MACD line below signal')
    if sk is not None and ss is not None:
        if sk>ss and sk<80:up+=1;reasons.append(f'Stochastic K {sk:.1f} rising')
        elif sk<ss and sk>20:down+=1;reasons.append(f'Stochastic K {sk:.1f} falling')
        elif sk>=90:down+=1;conflicts+=1;reasons.append(f'Stochastic {sk:.1f} extreme high')
        elif sk<=10:up+=1;conflicts+=1;reasons.append(f'Stochastic {sk:.1f} extreme low')
    if ax is not None and ax>=25:
        if di_p>di_m:up+=2;reasons.append(f'ADX {ax:.1f} strong +DI trend')
        elif di_m>di_p:down+=2;reasons.append(f'ADX {ax:.1f} strong -DI trend')
    elif ax is not None:reasons.append(f'ADX {ax:.1f} weak trend filter')
    if last['close']>last['open'] and last['close']>=prev['close']:up+=1;reasons.append('1m bullish momentum')
    if last['close']<last['open'] and last['close']<=prev['close']:down+=1;reasons.append('1m bearish momentum')
    pat=candle_pattern(last,prev)
    if pat in ('hammer_bullish','bullish_engulfing'):up+=2;reasons.append(pat.replace('_',' '))
    elif pat in ('shooting_star_bearish','bearish_engulfing'):down+=2;reasons.append(pat.replace('_',' '))
    price=last['close']
    if av:
        if abs(price-support)<=av*.35:up+=1;reasons.append('Price near support')
        if abs(resistance-price)<=av*.35:down+=1;reasons.append('Price near resistance')
        if last['high']-last['low']<av*.35:conflicts+=1;reasons.append('Very low-range candle')
    direction='UP' if up>down else 'DOWN' if down>up else None;edge=abs(up-down);conf=int(max(0,min(95,50+min(38,edge*4)-min(8,conflicts*2))))
    if direction is None:return {'decision':'NO_SIGNAL','confidence':conf,'direction':None,'reasons':['Technical consensus tied']+reasons[-5:]}
    if ax is not None and ax<18:return {'decision':'NO_SIGNAL','confidence':conf,'direction':direction,'reasons':reasons[-5:]+['ADX below 18: trend too weak']}
    if edge<3:return {'decision':'NO_SIGNAL','confidence':conf,'direction':direction,'reasons':reasons[-5:]+['Consensus edge too weak']}
    if conf<MIN_CONFIDENCE:return {'decision':'NO_SIGNAL','confidence':conf,'direction':direction,'reasons':reasons[-5:]+[f'Confidence {conf}% below {MIN_CONFIDENCE}% threshold']}
    return {'decision':'SIGNAL','confidence':conf,'direction':direction,'reasons':reasons[-7:],'indicators':{'EMA9':e9,'EMA21':e21,'EMA50':e50,'RSI':rv,'ATR':av,'MACD':ml,'MACD_signal':ms,'MACD_hist':md,'StochK':sk,'StochD':ss,'ADX':ax,'DI+':di_p,'DI-':di_m,'support':support,'resistance':resistance,'pattern':pat}}

def telegram(text):
    if not TOKEN or not CHAT_ID:return False
    try:r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHAT_ID,'text':text,'disable_web_page_preview':True},timeout=15);r.raise_for_status();return True
    except Exception as e:log.exception('Telegram error: %s',e);return False

def telegram_command_loop():
    global telegram_offset
    if not TOKEN or not CHAT_ID:log.warning('Telegram command listener disabled: token/chat id not configured');return
    log.info('Telegram command listener started')
    while True:
        try:
            r=requests.get(f'https://api.telegram.org/bot{TOKEN}/getUpdates',params={'timeout':25,'offset':telegram_offset+1,'allowed_updates':json.dumps(['message'])},timeout=35);r.raise_for_status();data=r.json()
            for u in data.get('result',[]):
                telegram_offset=max(telegram_offset,u.get('update_id',telegram_offset));m=u.get('message') or {};chat=str((m.get('chat') or {}).get('id',''));text=str(m.get('text','')).strip().lower()
                if chat!=CHAT_ID:continue
                if text.startswith('/start'):telegram('🎯 CANDICE AI 11.0\n\n✅ ONLINE\n📡 Olymp Trade • LIVE READ-ONLY\n🕐 1m candles\n🧠 EMA • RSI • MACD • Stoch • ADX • ATR • S/R • Price Action\n⏱️ 2 / 3 / 5 / 10 / 15 min\n🛡️ DEMO / MANUAL ONLY\n🚫 AUTO-TRADE OFF • MARTINGALE OFF\n\n/status • connection\n/performance • session')
                elif text.startswith('/status'):
                    s=live_feed.status() if live_feed else {'connected':False,'assets':[]};telegram(f'🎯 CANDICE STATUS\n\n📡 Olymp • {"🟢 CONNECTED" if s.get("connected") else "🔴 NOT CONNECTED"}\n📈 Assets • {", ".join(s.get("assets",[])) or "-"}\n🕐 1m\n🧠 Full brain • ON\n🛡️ Read-only • YES\n🚫 Auto-trade • OFF')
                elif text.startswith('/performance'):
                    reset_risk();telegram(f'📊 SESSION\nSignals • {risk["signals"]}\nWins • {risk["wins"]}\nLosses • {risk["losses_total"]}\nStreak • {risk["streak"]}\nDaily stop • {MAX_DAILY_LOSSES}\nConsecutive stop • {MAX_CONSECUTIVE_LOSSES}')
        except Exception as e:log.warning('Telegram command listener error: %s',e);time.sleep(5)

def seed_olymp_history(asset,candle):candles.setdefault(asset,deque(maxlen=300)).append(candle)

def process(p):
    global last_webhook
    reset_risk();asset=str(p.get('asset',p.get('symbol','UNKNOWN'))).upper().strip() or 'UNKNOWN';raw=normalize(p.get('candles',p.get('bars',[]))) or normalize([p])
    if raw:q=candles.setdefault(asset,deque(maxlen=300));q.extend(raw);data=list(q)
    else:data=list(candles.get(asset,[]))
    last_webhook=time.time();tech=analyze(data)
    if tech['decision']!='SIGNAL':return {'ok':True,'status':'NO_SIGNAL','asset':asset,'technical':tech}
    if risk['losses']>=MAX_DAILY_LOSSES or risk['streak']>=MAX_CONSECUTIVE_LOSSES:return {'ok':True,'status':'RISK_STOP','asset':asset,'reason':'Risk limit reached'}
    expiry=int(p.get('expiry',p.get('duration',5)) or 5);expiry=min(EXPIRIES,key=lambda x:abs(x-expiry));entry=data[-1]['close'];ts=data[-1]['timestamp'];key=f'{asset}:{tech["direction"]}:{expiry}:{int(ts//60)}'
    if key in sent:return {'ok':True,'status':'DUPLICATE_BLOCKED','asset':asset}
    sent.add(key);risk['signals']+=1;ind=tech.get('indicators',{})
    text=('━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • DEMO SIGNAL\n━━━━━━━━━━━━━━━━━━━━\n'+f'📈 Asset • {asset}\n➡️ Direction • {"🟢⬆️ UP" if tech["direction"]=="UP" else "🔴⬇️ DOWN"}\n💰 Entry • {entry}\n⏱️ Expiry • {expiry} min\n🧠 Confidence • {tech["confidence"]}%\n\n🔎 FULL BRAIN CHECK\n'+'\n'.join('• '+x for x in tech['reasons'])+f'\n\n📊 RSI {ind.get("RSI",0) or 0:.1f} • ADX {ind.get("ADX",0) or 0:.1f} • ATR {ind.get("ATR",0) or 0:.4f}\n📍 S {ind.get("support",0) or 0:.4f} • R {ind.get("resistance",0) or 0:.4f}\n🕐 1-minute • Olymp live market data\n🛡️ READ-ONLY • MANUAL ONLY\n🚫 AUTO-TRADE OFF • MARTINGALE OFF\n━━━━━━━━━━━━━━━━━━━━')
    ok=telegram(text);return {'ok':True,'status':'SIGNAL_SENT' if ok else 'SIGNAL_READY','asset':asset,'direction':tech['direction'],'confidence':tech['confidence'],'expiry':expiry,'telegram':ok}

def on_olymp_candle(asset,candle):
    try:process({'asset':asset,'candles':[candle],'expiry':5})
    except Exception:log.exception('Olymp candle processing error: %s',asset)

@app.get('/')
def root():return jsonify({'name':'Candice AI','version':VERSION,'mode':'DEMO_SIGNAL_ONLY','auto_trade':False,'martingale':False,'market_source':'Olymp Trade live read-only','brain':'EMA+RSI+MACD+STOCHASTIC+ADX+ATR+SUPPORT_RESISTANCE+PRICE_ACTION'})
@app.get('/health')
def health():
    reset_risk();return jsonify({'ok':True,'version':VERSION,'mode':'DEMO_SIGNAL_ONLY','auto_trade':False,'martingale':False,'timeframe':'1m','expiries':EXPIRIES,'assets':len(candles),'telegram_configured':bool(TOKEN and CHAT_ID),'last_webhook_at':last_webhook,'risk':risk,'brain':'FULL_TECHNICAL_CONSENSUS','olymp_live':live_feed.status() if live_feed else {'configured':False,'connected':False,'assets':[],'mode':'READ_ONLY_MARKET_DATA','auto_trade':False}})
@app.post('/webhook/tradingview')
def webhook():
    if SECRET and ((request.headers.get('X-Candice-Secret','') or request.args.get('secret',''))!=SECRET):return jsonify({'ok':False,'error':'unauthorized'}),401
    p=request.get_json(silent=True)
    if not isinstance(p,dict):
        try:p=json.loads(request.get_data(as_text=True) or '{}')
        except:return jsonify({'ok':False,'error':'invalid_json'}),400
    try:return jsonify(process(p))
    except Exception as e:log.exception('Webhook error: %s',e);return jsonify({'ok':False,'error':'internal_processing_error'}),500
@app.post('/result')
def result():
    p=request.get_json(silent=True) or {};o=str(p.get('result','')).upper();reset_risk()
    if o=='WIN':risk['wins']+=1;risk['streak']=0
    elif o=='LOSS':risk['losses']+=1;risk['losses_total']+=1;risk['streak']+=1
    else:return jsonify({'ok':False,'error':'result must be WIN or LOSS'}),400
    return jsonify({'ok':True,'risk':risk})

if __name__=='__main__':
    live_feed=OlympLiveFeed(on_olymp_candle,seed_olymp_history);live_feed.start();threading.Thread(target=telegram_command_loop,daemon=True).start();app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')),threaded=True)

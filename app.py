from __future__ import annotations
import json, logging, os, time, threading
from collections import deque
from datetime import datetime, timezone
from typing import Any
import requests
from flask import Flask, jsonify, request
from olymp_live import OlympLiveFeed

VERSION='10.2-CANDICE-OLYMP-LIVE-TELEGRAM'
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
    d=datetime.now(timezone.utc).date().isoformat()
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

def rsi(v,p=14):
    if len(v)<=p:return None
    d=[b-a for a,b in zip(v[-p-1:-1],v[-p:])]; g=sum(max(x,0) for x in d)/p; l=sum(max(-x,0) for x in d)/p
    return 100 if l==0 else 100-100/(1+g/l)

def atr(data,p=14):
    if len(data)<=p:return None
    z=[]
    for a,b in zip(data[-p-1:-1],data[-p:]):z.append(max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close'])))
    return sum(z)/p

def analyze(data):
    if len(data)<30:return {'decision':'NO_SIGNAL','confidence':0,'direction':None,'reasons':['Need 30+ one-minute candles']}
    c=[x['close'] for x in data]; e9=ema(c,9); e21=ema(c,21); e50=ema(c,50); rv=rsi(c); av=atr(data); last=data[-1]; prev=data[-2]
    up=down=0; reasons=[]
    if e9 and e21:
        if e9>e21:up+=2;reasons.append('EMA9 > EMA21 bullish structure')
        else:down+=2;reasons.append('EMA9 < EMA21 bearish structure')
    if e21 and e50:
        if e21>e50:up+=1;reasons.append('EMA21 > EMA50 trend support')
        else:down+=1;reasons.append('EMA21 < EMA50 trend pressure')
    if rv is not None:
        if 52<=rv<=68:up+=1;reasons.append(f'RSI {rv:.1f} supports upside')
        elif 32<=rv<=48:down+=1;reasons.append(f'RSI {rv:.1f} supports downside')
        elif rv>75:down+=1;reasons.append(f'RSI {rv:.1f} overbought warning')
        elif rv<25:up+=1;reasons.append(f'RSI {rv:.1f} oversold warning')
    if last['close']>last['open'] and last['close']>=prev['close']:up+=1;reasons.append('Latest 1m candle confirms bullish momentum')
    if last['close']<last['open'] and last['close']<=prev['close']:down+=1;reasons.append('Latest 1m candle confirms bearish momentum')
    rng=max(last['high']-last['low'],1e-12)
    if abs(last['close']-last['open'])/rng>=.55:
        if last['close']>last['open']:up+=1;reasons.append('Strong bullish candle body')
        elif last['close']<last['open']:down+=1;reasons.append('Strong bearish candle body')
    if av and rng<av*.35:up=max(0,up-1);down=max(0,down-1);reasons.append('Low-range candle reduces confidence')
    if up==down:return {'decision':'NO_SIGNAL','confidence':0,'direction':None,'reasons':reasons[-4:]}
    direction='UP' if up>down else 'DOWN'; lead=max(up,down); conf=min(92,55+lead*7+abs(up-down)*3)
    if conf<MIN_CONFIDENCE:return {'decision':'NO_SIGNAL','confidence':conf,'direction':direction,'reasons':reasons[-4:]+[f'Confidence {conf}% below {MIN_CONFIDENCE}% threshold']}
    return {'decision':'SIGNAL','confidence':conf,'direction':direction,'reasons':reasons[-4:]}

def telegram(text):
    if not TOKEN or not CHAT_ID:return False
    try:
        r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHAT_ID,'text':text,'disable_web_page_preview':True},timeout=15);r.raise_for_status();return True
    except Exception as e:log.exception('Telegram error: %s',e);return False

def telegram_command_loop():
    global telegram_offset
    if not TOKEN or not CHAT_ID:
        log.warning('Telegram command listener disabled: token/chat id not configured'); return
    log.info('Telegram command listener started')
    while True:
        try:
            r=requests.get(f'https://api.telegram.org/bot{TOKEN}/getUpdates',params={'timeout':25,'offset':telegram_offset+1,'allowed_updates':json.dumps(['message'])},timeout=35); r.raise_for_status(); data=r.json()
            for u in data.get('result',[]):
                telegram_offset=max(telegram_offset,u.get('update_id',telegram_offset))
                m=u.get('message') or {}; chat=str((m.get('chat') or {}).get('id','')); text=str(m.get('text','')).strip().lower()
                if chat!=CHAT_ID: continue
                if text.startswith('/start'):
                    telegram('━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI\n━━━━━━━━━━━━━━━━━━━━\n✅ Bot is ONLINE\n📡 Olymp Trade • LIVE READ-ONLY MARKET DATA\n🕐 1-minute candle analysis\n⏱️ Expiry • 2 / 3 / 5 / 10 / 15 min\n🧠 AI signal engine • DEMO\n🛡️ MANUAL ONLY • AUTO-TRADE OFF\n🚫 MARTINGALE OFF\n\nUse /status to check live connection.\n━━━━━━━━━━━━━━━━━━━━')
                elif text.startswith('/status'):
                    s=live_feed.status() if live_feed else {'configured':False,'connected':False,'assets':[]}
                    telegram(f'🎯 CANDICE AI STATUS\n\n📡 Olymp feed • {"🟢 CONNECTED" if s.get("connected") else "🔴 NOT CONNECTED"}\n📈 Assets • {", ".join(s.get("assets",[])) or "-"}\n🕐 Timeframe • 1m\n🛡️ Read-only • YES\n🚫 Auto-trade • OFF\n🚫 Martingale • OFF')
                elif text.startswith('/help'):
                    telegram('🎯 CANDICE AI COMMANDS\n\n/start • Start bot\n/status • Live connection status\n/help • Commands')
        except Exception as e:
            log.warning('Telegram command listener error: %s',e)
            time.sleep(5)

def process(p):
    global last_webhook
    reset_risk();asset=str(p.get('asset',p.get('symbol','UNKNOWN'))).upper().strip() or 'UNKNOWN'; raw=normalize(p.get('candles',p.get('bars',[]))) or normalize([p])
    if raw:q=candles.setdefault(asset,deque(maxlen=300));q.extend(raw);data=list(q)
    else:data=list(candles.get(asset,[]))
    last_webhook=time.time();tech=analyze(data)
    if tech['decision']!='SIGNAL':return {'ok':True,'status':'NO_SIGNAL','asset':asset,'technical':tech}
    if risk['losses']>=MAX_DAILY_LOSSES or risk['streak']>=MAX_CONSECUTIVE_LOSSES:return {'ok':True,'status':'RISK_STOP','asset':asset}
    expiry=int(p.get('expiry',p.get('duration',5)) or 5);expiry=min(EXPIRIES,key=lambda x:abs(x-expiry));entry=data[-1]['close'];ts=data[-1]['timestamp'];key=f'{asset}:{tech["direction"]}:{expiry}:{int(ts//60)}'
    if key in sent:return {'ok':True,'status':'DUPLICATE_BLOCKED','asset':asset}
    sent.add(key);risk['signals']+=1
    text=('━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • DEMO SIGNAL\n━━━━━━━━━━━━━━━━━━━━\n'+f'📈 Asset • {asset}\n➡️ Direction • {"🟢⬆️ UP" if tech["direction"]=="UP" else "🔴⬇️ DOWN"}\n💰 Entry • {entry}\n⏱️ Expiry • {expiry} min\n🧠 Confidence • {tech["confidence"]}%\n\n🔎 VERIFIED REASONS\n'+'\n'.join('• '+x for x in tech['reasons'])+'\n\n🕐 Timeframe • 1-minute\n📡 Source • Olymp Trade live market data\n🛡️ READ-ONLY MARKET FEED • MANUAL ONLY\n🚫 AUTO-TRADE OFF • MARTINGALE OFF\n━━━━━━━━━━━━━━━━━━━━')
    ok=telegram(text);return {'ok':True,'status':'SIGNAL_SENT' if ok else 'SIGNAL_READY','asset':asset,'direction':tech['direction'],'confidence':tech['confidence'],'expiry':expiry,'telegram':ok}

def on_olymp_candle(asset: str,candle: dict)->None:
    try:process({'asset':asset,'candles':[candle],'expiry':5})
    except Exception:log.exception('Olymp candle processing error: %s',asset)

@app.get('/')
def root():return jsonify({'name':'Candice AI','version':VERSION,'mode':'DEMO_SIGNAL_ONLY','auto_trade':False,'martingale':False,'market_source':'Olymp Trade live read-only'})
@app.get('/health')
def health():
    reset_risk();return jsonify({'ok':True,'version':VERSION,'mode':'DEMO_SIGNAL_ONLY','auto_trade':False,'martingale':False,'timeframe':'1m','expiries':EXPIRIES,'assets':len(candles),'telegram_configured':bool(TOKEN and CHAT_ID),'last_webhook_at':last_webhook,'risk':risk,'olymp_live':live_feed.status() if live_feed else {'configured':False,'connected':False,'assets':[],'mode':'READ_ONLY_MARKET_DATA','auto_trade':False}})
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
    live_feed=OlympLiveFeed(on_olymp_candle);live_feed.start()
    threading.Thread(target=telegram_command_loop,daemon=True).start()
    app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')),threaded=True)

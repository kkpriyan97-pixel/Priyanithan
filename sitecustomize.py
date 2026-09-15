"""Candice runtime overlay: FLEX Binary only, continuous 1m scan, rolling 5m decision windows, read-only only."""
from __future__ import annotations
import json, logging, os, sys, threading, time, requests
LOG=logging.getLogger('candice.runtime')
TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','').strip(); CONFIGURED_CHAT=os.getenv('TELEGRAM_CHAT_ID','').strip(); ACCESS_CODE=os.getenv('CANDICE_ACCESS_CODE','').strip()
LOCK=threading.RLock(); ACTIVE_CHAT=CONFIGURED_CHAT; AUTHORIZED=bool(CONFIGURED_CHAT); POLL_STARTED=False; INSTALLED=False
CANDIDATES={}; SENT_WINDOWS=set(); EXPIRIES={2,3,5,15}; LIVE_RECEIPTS={}; SESSION=requests.Session()

def send(chat,text):
    if not TOKEN or not chat:return False
    try:
        r=SESSION.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':chat,'text':text,'disable_web_page_preview':True},timeout=15)
        if r.ok:return True
        try: desc=r.json().get('description','unknown')
        except Exception: desc='unknown'
        LOG.warning('Telegram control-message failed: HTTP %s | %s',r.status_code,desc)
        return False
    except Exception as e:
        LOG.warning('Telegram control-message failed: %s',type(e).__name__);return False

def handle(u):
    global ACTIVE_CHAT,AUTHORIZED
    m=u.get('message') or {}; cid=str((m.get('chat') or {}).get('id','')).strip(); text=str(m.get('text','')).strip(); low=text.lower()
    if not cid or not text:return
    if low.startswith('/start'):
        with LOCK:
            if not CONFIGURED_CHAT or cid==CONFIGURED_CHAT:ACTIVE_CHAT,AUTHORIZED=cid,True
        send(cid,'🎯 CANDICE AI\n\n'+('✅ ONLINE\n🔐 ACCESS • AUTHORIZED\n📡 MARKET • READ-ONLY\n🚫 AUTO-TRADE • OFF\n🚫 MARTINGALE • OFF\n\n/status • connection\n/performance • session' if AUTHORIZED and cid==ACTIVE_CHAT else '🔐 ACCESS REQUIRED\n\nSend /access YOUR_ACCESS_CODE'));return
    if low.startswith('/access '):
        ok=bool(ACCESS_CODE) and text.split(' ',1)[1].strip().casefold()==ACCESS_CODE.casefold()
        with LOCK:
            if ok:ACTIVE_CHAT,AUTHORIZED=cid,True
        send(cid,'🎯 CANDICE AI\n\n✅ ONLINE\n🔐 ACCESS • AUTHORIZED\n📡 MARKET • READ-ONLY\n🚫 AUTO-TRADE • OFF\n🚫 MARTINGALE • OFF' if ok else '❌ ACCESS • DENIED');return
    with LOCK:allowed=AUTHORIZED and cid==ACTIVE_CHAT
    if not allowed:return
    mod=sys.modules.get('__main__')
    if low.startswith('/status'):
        try:
            s=mod.live_feed.status() if getattr(mod,'live_feed',None) else {};send(cid,f"🎯 CANDICE STATUS\n\n📡 Olymp • {'🟢 CONNECTED' if s.get('connected') else '🔴 DISCONNECTED'}\n📈 FLEX assets • {len(s.get('tradeable_assets') or [])}\n🕐 Real 1m candles • ON\n🧠 Brain • ON\n📊 Continuous 1m scan • ON\n🛡️ READ-ONLY • YES\n🚫 Auto-trade • OFF\n🚫 Martingale • OFF")
        except Exception:send(cid,'🔴 Status temporarily unavailable')
    elif low.startswith('/performance'):
        try:
            from outcome_engine import performance
            p=performance();send(cid,f"📊 CANDICE PERFORMANCE\n\nSignals evaluated • {p['evaluated']}\nWins • {p['wins']}\nLosses • {p['losses']}\nTies • {p['ties']}\nWin rate • {p['win_rate']}%")
        except Exception:send(cid,'📊 Performance is not ready yet.')

def poller():
    global POLL_STARTED
    with LOCK:
        if POLL_STARTED:return
        POLL_STARTED=True
    if not TOKEN:LOG.warning('Telegram poller disabled: token missing');return
    offset=0;LOG.info('TELEGRAM_OWNER_POLLER started')
    while True:
        try:
            r=SESSION.get(f'https://api.telegram.org/bot{TOKEN}/getUpdates',params={'timeout':25,'offset':offset+1,'allowed_updates':json.dumps(['message'])},timeout=35)
            if r.status_code in (409,429):time.sleep(10);continue
            if r.status_code==401:LOG.error('Telegram token rejected');time.sleep(30);continue
            r.raise_for_status()
            for u in r.json().get('result',[]):
                offset=max(offset,int(u.get('update_id',offset)))
                try:handle(u)
                except Exception:LOG.exception('Telegram update handling failed')
        except Exception:time.sleep(5)

def _age(c,asset=None):
    now=time.time()
    if asset:
        try:
            with LOCK: rec=LIVE_RECEIPTS.get(asset)
            if rec:
                candle_ts,received_at=rec
                if received_at<=now+5 and (not candle_ts or candle_ts<=now+75): return max(0,now-received_at)
        except Exception:pass
    try:
        r=float(c.get('received_at',0))
        if r>0 and r<=now+5:return max(0,now-r)
    except Exception:pass
    try:
        t=float(c.get('timestamp',0))
        if t>1e10:t/=1000
        if t>0:return max(0,now-t)
    except Exception:pass
    return 10**9

def _install_runtime():
    global INSTALLED
    while not INSTALLED:
        mod=sys.modules.get('__main__')
        if mod is None or not all(hasattr(mod,n) for n in ('analyze','on_olymp_candle','send_signal','candles')):
            time.sleep(.2);continue
        try:
            from strategy_brain import evaluate as brain_evaluate
            from outcome_engine import on_candle as outcome_on_candle, register as outcome_register
            original_candle=mod.on_olymp_candle; original_send=mod.send_signal; original_telegram=getattr(mod,'telegram',None)

            def brain_analyze(data):
                asset=getattr(threading.current_thread(),'candice_asset',None) or 'UNKNOWN'
                b=brain_evaluate(asset,data,{'mtf':{'1m':0,'3m':0,'5m':0}})
                if b.get('allow'):
                    return {'decision':'SIGNAL','direction':b['direction'],'confidence':b['score'],'reasons':b.get('reasons',[]),'brain':b,'mtf':{},'indicators':{}}
                return {'decision':'NO_SIGNAL','direction':b.get('direction'),'confidence':b.get('score',0),'reasons':b.get('reasons',[]),'brain':b,'mtf':{},'indicators':{}}

            def flex_assets():
                """Live account-available/tradeable assets only; no fixed Forex universe."""
                try:
                    feed=getattr(mod,'live_feed',None)
                    if not feed:return set()
                    return set(feed.status().get('tradeable_assets') or [])
                except Exception:return set()

            def runtime_telegram(text):
                """Route signal delivery to the authorized runtime chat, not a stale fixed chat id."""
                with LOCK:
                    target=ACTIVE_CHAT if AUTHORIZED and ACTIVE_CHAT else CONFIGURED_CHAT
                if not target:
                    LOG.warning('Telegram signal blocked: no authorized chat configured')
                    return False
                try:
                    r=SESSION.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':target,'text':text,'disable_web_page_preview':True},timeout=15)
                    if r.ok:
                        LOG.info('TELEGRAM_SIGNAL_DELIVERED chat=%s',target)
                        return True
                    try: desc=r.json().get('description','unknown')
                    except Exception: desc='unknown'
                    LOG.error('TELEGRAM_SIGNAL_FAILED chat=%s HTTP=%s | %s',target,r.status_code,desc)
                    if r.status_code==403:
                        LOG.error('TELEGRAM_403_ACTION_REQUIRED chat=%s | unblock/start bot or grant send permission in target chat',target)
                    return False
                except Exception as e:
                    LOG.error('TELEGRAM_SIGNAL_FAILED chat=%s | %s',target,type(e).__name__)
                    return False

            def capture_send(asset,data,tech,expiry=5):
                if tech.get('decision')=='SIGNAL':
                    window=int(time.time()//300)+1; b=tech.get('brain') or {}
                    key=f'{asset}:{window}'
                    with LOCK:
                        CANDIDATES[key]={'asset':asset,'window':window,'created':time.time(),'quality':int(b.get('score',0)),'expiry':int(b.get('expiry',expiry) or 5),'direction':b.get('direction'),'brain':b}
                    LOG.info('BRAIN_CANDIDATE asset=%s window=%s quality=%s direction=%s',asset,window,b.get('score'),b.get('direction'))
                return {'ok':True,'status':'DEFERRED'}

            mod.analyze=brain_analyze;mod.send_signal=capture_send
            if original_telegram: mod.telegram=runtime_telegram

            def wrapped_candle(asset,candle):
                c=dict(candle);received=time.time();c.setdefault('received_at',received)
                try:
                    ts=float(c.get('timestamp',0));ts=ts/1000 if ts>1e10 else ts
                    if ts>0 and ts<=received+2 and received-ts<=75:
                        with LOCK:LIVE_RECEIPTS[asset]=(ts,received)
                except Exception:pass
                try:outcome_on_candle(asset,c,getattr(mod,'telegram',None))
                except Exception:LOG.exception('Outcome processing failed asset=%s',asset)
                threading.current_thread().candice_asset=asset;original_candle(asset,c)
            mod.on_olymp_candle=wrapped_candle

            def scheduler():
                LOG.info('BRAIN_SCHEDULER started: FLEX Binary only; continuous 1m scan; 5m windows; hard pre-boundary final')
                last_window=None;last_research_minute=None;last_decision_window=None
                while True:
                    try:
                        now=time.time();window=int(now//300);minute=int(now//60)
                        if window!=last_window:
                            last_window=window;LOG.info('BRAIN_WINDOW_OPEN window=%s | FLEX_BINARY_ONLY',window)
                        if minute!=last_research_minute and int(now)%60>=8:
                            last_research_minute=minute;allowed=flex_assets()
                            with LOCK:snapshot={a:list(v) for a,v in mod.candles.items() if a in allowed}
                            assets_available=len(allowed);scanned=0;fresh=0;qualified=0;rejected=0;top=[]
                            for asset,data in snapshot.items():
                                if len(data)<30:continue
                                scanned+=1;age=_age(data[-1],asset)
                                if age<=75:
                                    fresh+=1
                                    try:
                                        threading.current_thread().candice_asset=asset;tech=brain_analyze(data)
                                        if tech.get('decision')=='SIGNAL':qualified+=1;top.append((int(tech.get('confidence',0)),asset,tech.get('direction') or '?',age))
                                        else:rejected+=1
                                    except Exception:rejected+=1
                            top.sort(reverse=True);ranking=' | '.join(f'#{i+1} {a} {d} {c}% age={age:.1f}s' for i,(c,a,d,age) in enumerate(top[:5])) if top else 'No qualified setup in this minute'
                            next_decision=(window+1)*300
                            LOG.info('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');LOG.info('🧠 CANDICE BRAIN • 1-MIN RESEARCH | minute=%s',minute)
                            LOG.info('📊 FLEX assets available=%s | scanned=%s | fresh verified=%s | rejected/weak=%s | qualified=%s',assets_available,scanned,fresh,rejected,qualified)
                            LOG.info('🏆 TOP FLEX RANKING | %s',ranking)
                            LOG.info('🕯️ REAL 1M CANDLE RESEARCH | completed live data only | freshness cutoff=75s')
                            LOG.info('🧠 Rolling-hour candidate memory=%s | current 5M window=%s | final deadline=%s',len(CANDIDATES),window,time.strftime('%H:%M:%S',time.localtime(next_decision)))
                            LOG.info('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
                        sec=int(now)%300
                        if AUTHORIZED and window not in SENT_WINDOWS and 240<=sec<300 and last_decision_window!=window:
                            last_decision_window=window;allowed=flex_assets()
                            with LOCK:pool=[v for v in CANDIDATES.values() if v.get('window')==window and v.get('asset') in allowed]
                            ranked=[]
                            for cand in pool:
                                asset=cand['asset']
                                with LOCK:data=list(mod.candles.get(asset,[]))
                                if len(data)<30:continue
                                age=_age(data[-1],asset)
                                if age>75:continue
                                threading.current_thread().candice_asset=asset;freshtech=brain_analyze(data)
                                if freshtech.get('decision')=='SIGNAL':ranked.append((int(freshtech.get('confidence',0)),asset,data,freshtech,cand,'RESCORED'))
                                elif int(cand.get('quality',0))>=58:
                                    fallback={'decision':'SIGNAL','direction':cand.get('direction'),'confidence':cand.get('quality',0),'reasons':['current-window qualified candidate; fresh live candle recheck retained'],'brain':cand.get('brain') or {},'mtf':{},'indicators':{}}
                                    ranked.append((int(cand.get('quality',0)),asset,data,fallback,cand,'RETAINED'))
                                    LOG.info('WINDOW_CANDIDATE_RETAINED asset=%s quality=%s fresh_age=%.1fs',asset,cand.get('quality'),age)
                            if ranked:
                                ranked.sort(key=lambda x:(x[0],x[4].get('created',0)),reverse=True);_,asset,data,tech,cand,mode=ranked[0]
                                expiry=int((tech.get('brain') or {}).get('expiry',cand.get('expiry',5)) or 5);expiry=expiry if expiry in EXPIRIES else 5
                                with LOCK:SENT_WINDOWS.add(window)
                                result=original_send(asset,data,tech,expiry)
                                if result.get('status')=='SIGNAL':
                                    b=tech.get('brain') or {}
                                    try:outcome_register({'status':'SIGNAL','asset':asset,'direction':tech.get('direction'),'expiry':expiry,'confidence':tech.get('confidence',0),'timestamp':float(data[-1].get('timestamp',time.time())),'entry':float(data[-1].get('close',0)),'strategy':b.get('strategy','market_brain'),'regime':b.get('regime',''),'pattern':b.get('pattern',''),'features':b})
                                    except Exception:LOG.exception('Outcome registration failed asset=%s',asset)
                                    LOG.info('WINDOW_SIGNAL window=%s asset=%s direction=%s expiry=%s confidence=%s mode=%s fresh_age=%.1fs',window,asset,tech.get('direction'),expiry,tech.get('confidence'),mode,_age(data[-1],asset))
                                else:
                                    with LOCK:SENT_WINDOWS.discard(window);last_decision_window=None
                                    LOG.warning('WINDOW_SIGNAL_FAILED window=%s status=%s asset=%s',window,result.get('status'),asset)
                            else:LOG.info('WINDOW_NO_VALID_FLEX_SIGNAL window=%s deadline_in=%ss',window,300-sec)
                        with LOCK:
                            cutoff=window-13
                            for k,v in list(CANDIDATES.items()):
                                if v.get('window',-999)<cutoff:CANDIDATES.pop(k,None)
                            for a,(ts,rec) in list(LIVE_RECEIPTS.items()):
                                if now-rec>180:LIVE_RECEIPTS.pop(a,None)
                        time.sleep(1)
                    except Exception:LOG.exception('BRAIN_SCHEDULER_ERROR');time.sleep(2)
            threading.Thread(target=scheduler,name='candice-brain-scheduler',daemon=True).start();INSTALLED=True;LOG.info('CANDICE_BRAIN_OVERLAY installed | FLEX_BINARY_ONLY | TELEGRAM_RUNTIME_ROUTING=ON')
        except Exception:LOG.exception('Brain overlay install failed');time.sleep(2)
threading.Thread(target=poller,name='candice-telegram-owner',daemon=True).start()
threading.Thread(target=_install_runtime,name='candice-runtime-installer',daemon=True).start()

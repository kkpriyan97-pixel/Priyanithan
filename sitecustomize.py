"""Candice runtime overlay: continuous brain scan, rolling 5-minute decision windows, read-only only."""
from __future__ import annotations
import json, logging, os, sys, threading, time, requests
LOG=logging.getLogger('candice.runtime')
TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','').strip(); CONFIGURED_CHAT=os.getenv('TELEGRAM_CHAT_ID','').strip(); ACCESS_CODE=os.getenv('CANDICE_ACCESS_CODE','').strip()
LOCK=threading.RLock(); ACTIVE_CHAT=CONFIGURED_CHAT; AUTHORIZED=bool(CONFIGURED_CHAT); POLL_STARTED=False; INSTALLED=False
CANDIDATES={}; SENT_WINDOWS=set(); EXPIRIES={1,2,3,4,5,10,15}; LIVE_RECEIPTS={}; SESSION=requests.Session()
def send(chat,text):
    if not TOKEN or not chat:return
    try:SESSION.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':chat,'text':text,'disable_web_page_preview':True},timeout=15)
    except Exception:LOG.exception('Telegram send failed')
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
            s=mod.live_feed.status() if getattr(mod,'live_feed',None) else {};send(cid,f"🎯 CANDICE STATUS\n\n📡 Olymp • {'🟢 CONNECTED' if s.get('connected') else '🔴 DISCONNECTED'}\n📈 Assets • {len(s.get('assets') or [])}\n🕐 Real 1m candles • ON\n🧠 Brain • ON\n📊 Continuous 1m scan • ON\n🛡️ READ-ONLY • YES\n🚫 Auto-trade • OFF\n🚫 Martingale • OFF")
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
    # Primary source: receipt time captured directly at the live-candle callback.
    if asset:
        try:
            with LOCK: rec=LIVE_RECEIPTS.get(asset)
            if rec:
                candle_ts,received_at=rec
                if received_at<=now+5 and (not candle_ts or candle_ts<=now+75):
                    return max(0,now-received_at)
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
            original_candle=mod.on_olymp_candle; original_send=mod.send_signal
            def brain_analyze(data):
                asset=getattr(threading.current_thread(),'candice_asset',None) or 'UNKNOWN'
                b=brain_evaluate(asset,data,{'mtf':{'1m':0,'3m':0,'5m':0}})
                if b.get('allow'):
                    return {'decision':'SIGNAL','direction':b['direction'],'confidence':b['score'],'reasons':b.get('reasons',[]),'brain':b,'mtf':{},'indicators':{}}
                return {'decision':'NO_SIGNAL','direction':b.get('direction'),'confidence':b.get('score',0),'reasons':b.get('reasons',[]),'brain':b,'mtf':{},'indicators':{}}
            def capture_send(asset,data,tech,expiry=5):
                if tech.get('decision')=='SIGNAL':
                    b=tech.get('brain') or {}; window=int(float(data[-1].get('timestamp',time.time()))//300);key=f'{asset}:{window}'
                    with LOCK:CANDIDATES[key]={'asset':asset,'window':window,'created':time.time(),'quality':int(b.get('score',0)),'expiry':int(b.get('expiry',expiry) or 5)}
                    LOG.info('BRAIN_CANDIDATE asset=%s window=%s quality=%s direction=%s',asset,window,b.get('score'),b.get('direction'))
                return {'ok':True,'status':'DEFERRED'}
            mod.analyze=brain_analyze;mod.send_signal=capture_send
            def wrapped_candle(asset,candle):
                c=dict(candle); received=time.time(); c.setdefault('received_at',received)
                # Record only a completed candle that is plausibly current. This survives
                # any normalization/deque rewrite performed by the original app handler.
                try:
                    ts=float(c.get('timestamp',0));
                    if ts>1e10:ts/=1000
                    now=received
                    if ts>0 and ts<=now+2 and now-ts<=75:
                        with LOCK:LIVE_RECEIPTS[asset]=(ts,received)
                except Exception:pass
                try:outcome_on_candle(asset,c,getattr(mod,'telegram',None))
                except Exception:LOG.exception('Outcome processing failed asset=%s',asset)
                threading.current_thread().candice_asset=asset
                original_candle(asset,c)
            mod.on_olymp_candle=wrapped_candle
            def scheduler():
                LOG.info('BRAIN_SCHEDULER started: continuous 1m scan; 5m decision windows; previous-hour brain memory')
                last_window=None; last_research_minute=None; last_decision_window=None
                while True:
                    try:
                        now=time.time();window=int(now//300); minute=int(now//60)
                        if window!=last_window:
                            last_window=window;LOG.info('BRAIN_WINDOW_OPEN window=%s',window)
                        if minute!=last_research_minute and int(now)%60>=8:
                            last_research_minute=minute
                            with LOCK: snapshot={a:list(v) for a,v in mod.candles.items()}
                            assets_available=len(snapshot); scanned=0; fresh=0; qualified=0; rejected=0; top=[]
                            for asset,data in snapshot.items():
                                if len(data)<30: continue
                                scanned+=1; age=_age(data[-1],asset)
                                if age<=75:
                                    fresh+=1
                                    try:
                                        threading.current_thread().candice_asset=asset
                                        tech=brain_analyze(data)
                                        if tech.get('decision')=='SIGNAL':
                                            qualified+=1;top.append((int(tech.get('confidence',0)),asset,tech.get('direction') or '?',age))
                                        else: rejected+=1
                                    except Exception: rejected+=1
                            top.sort(reverse=True);top3=top[:3]
                            ranking=' | '.join(f'#{i+1} {a} {d} {c}% age={age:.1f}s' for i,(c,a,d,age) in enumerate(top3)) if top3 else 'No qualified setup in this minute'
                            next_decision=(window+1)*300
                            LOG.info('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
                            LOG.info('🧠 CANDICE BRAIN • 1-MIN RESEARCH | minute=%s',minute)
                            LOG.info('📊 Assets available=%s | scanned=%s | fresh verified=%s | rejected/weak=%s | qualified=%s',assets_available,scanned,fresh,rejected,qualified)
                            LOG.info('🏆 TOP RANKING | %s',ranking)
                            LOG.info('🕯️ REAL 1M CANDLE RESEARCH | completed live data only | freshness cutoff=75s')
                            LOG.info('🧠 Previous-hour candidate memory=%s | current 5M window=%s | next 5M decision=%s',len(CANDIDATES),window,time.strftime('%H:%M:%S',time.localtime(next_decision)))
                            LOG.info('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
                        if AUTHORIZED and window not in SENT_WINDOWS and int(now)%300>=285 and last_decision_window!=window:
                            last_decision_window=window
                            with LOCK:pool=list(CANDIDATES.values())
                            ranked=[]
                            for cand in pool:
                                asset=cand['asset']
                                with LOCK:data=list(mod.candles.get(asset,[]))
                                if len(data)<30 or _age(data[-1],asset)>75:continue
                                threading.current_thread().candice_asset=asset;tech=brain_analyze(data)
                                if tech.get('decision')=='SIGNAL':ranked.append((int(tech.get('confidence',0)),asset,data,tech,cand))
                            if ranked:
                                ranked.sort(key=lambda x:(x[0],x[4].get('created',0)),reverse=True);_,asset,data,tech,cand=ranked[0]
                                expiry=int((tech.get('brain') or {}).get('expiry',cand.get('expiry',5)) or 5);expiry=expiry if expiry in EXPIRIES else 5
                                with LOCK:SENT_WINDOWS.add(window)
                                result=original_send(asset,data,tech,expiry)
                                if result.get('status')=='SIGNAL':
                                    b=tech.get('brain') or {}
                                    try: outcome_register({'status':'SIGNAL','asset':asset,'direction':tech.get('direction'),'expiry':expiry,'confidence':tech.get('confidence',0),'timestamp':float(data[-1].get('timestamp',time.time())),'entry':float(data[-1].get('close',0)),'strategy':b.get('strategy','market_brain'),'regime':b.get('regime',''),'pattern':b.get('pattern',''),'features':b})
                                    except Exception:LOG.exception('Outcome registration failed asset=%s',asset)
                                if result.get('status') not in ('SIGNAL','WINDOW_ALREADY_SENT'):
                                    with LOCK:SENT_WINDOWS.discard(window);last_decision_window=None
                                else:LOG.info('WINDOW_SIGNAL window=%s asset=%s direction=%s expiry=%s confidence=%s fresh_age=%.1fs',window,asset,tech.get('direction'),expiry,tech.get('confidence'),_age(data[-1],asset))
                            else:LOG.info('WINDOW_WAIT window=%s no fresh qualified candidate',window)
                        with LOCK:
                            cutoff=window-13
                            for k,v in list(CANDIDATES.items()):
                                if v.get('window',-999)<cutoff:CANDIDATES.pop(k,None)
                            for a,(ts,rec) in list(LIVE_RECEIPTS.items()):
                                if now-rec>180:LIVE_RECEIPTS.pop(a,None)
                        time.sleep(1)
                    except Exception:LOG.exception('BRAIN_SCHEDULER_ERROR');time.sleep(2)
            threading.Thread(target=scheduler,name='candice-brain-scheduler',daemon=True).start();INSTALLED=True;LOG.info('CANDICE_BRAIN_OVERLAY installed')
        except Exception:LOG.exception('Brain overlay install failed');time.sleep(2)
threading.Thread(target=poller,name='candice-telegram-owner',daemon=True).start()
threading.Thread(target=_install_runtime,name='candice-runtime-installer',daemon=True).start()

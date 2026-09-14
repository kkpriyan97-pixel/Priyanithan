"""Candice runtime: read-only market brain, Telegram access, five-minute candidate ranking."""
from __future__ import annotations
import json, logging, os, re, sys, threading, time
import requests
from requests import Response

LOG=logging.getLogger('candice.runtime')
TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','').strip(); CONFIGURED_CHAT=os.getenv('TELEGRAM_CHAT_ID','').strip(); ACCESS_CODE=os.getenv('CANDICE_ACCESS_CODE','').strip()
# A configured Telegram chat is the owner's persistent authorization; Render restarts must not log it out.
ACTIVE_CHAT=CONFIGURED_CHAT; AUTHORIZED=bool(CONFIGURED_CHAT); LOCK=threading.RLock(); ORIGINAL_REQUEST=requests.sessions.Session.request
ACTIVE_SIGNALS={}; CANDIDATES={}; SENT_WINDOWS=set(); POLL_STARTED=False
SUPPORTED_EXPIRIES={1,2,3,4,5,10,15}

def _raw(method,path,**kwargs):
    if not TOKEN:return None
    try:return ORIGINAL_REQUEST(requests.Session(),method,f'https://api.telegram.org/bot{TOKEN}/{path}',**kwargs)
    except Exception:return None

def _raw_post(path,payload):return _raw('POST',path,json=payload,timeout=15)

def _send(chat,text):
    if chat:_raw_post('sendMessage',{'chat_id':chat,'text':text,'disable_web_page_preview':True})

def _handle_update(u):
    global ACTIVE_CHAT,AUTHORIZED
    m=u.get('message') or {}; c=m.get('chat') or {}; cid=str(c.get('id','')).strip(); text=str(m.get('text','')).strip()
    if not cid or not text:return
    low=text.lower()
    if low.startswith('/start'):
        with LOCK:
            if not CONFIGURED_CHAT or cid==CONFIGURED_CHAT: ACTIVE_CHAT=cid; AUTHORIZED=True
        if cid==ACTIVE_CHAT and AUTHORIZED:
            _send(cid,'🎯 CANDICE AI\n\n✅ ONLINE\n🔐 ACCESS • AUTHORIZED\n👤 Telegram session • BOUND\n📡 Market access • READ-ONLY\n🚫 Order access • DISABLED\n\n/status • connection\n/performance • session')
        else:_send(cid,'🔐 CANDICE AI ACCESS\n\nSend /access YOUR_ACCESS_CODE')
        return
    if low.startswith('/access '):
        supplied=text.split(' ',1)[1].strip(); ok=bool(ACCESS_CODE) and supplied.casefold()==ACCESS_CODE.casefold()
        with LOCK:
            if ok: ACTIVE_CHAT=cid; AUTHORIZED=True
        _send(cid,'🎯 CANDICE AI\n\n✅ ONLINE\n🔐 ACCESS • AUTHORIZED\n👤 Telegram session • BOUND\n📡 Market access • READ-ONLY\n🚫 Order access • DISABLED\n\n/status • connection\n/performance • session' if ok else '❌ ACCESS • DENIED')
        return
    with LOCK: allowed=(cid==ACTIVE_CHAT and AUTHORIZED)
    if not allowed:return
    if low.startswith('/status'):
        mod=sys.modules.get('__main__')
        try:s=mod.live_feed.status(); assets=s.get('assets') or s.get('discovered_assets') or []
        except Exception:assets=[]
        _send(cid,f"🎯 CANDICE STATUS\n\n📡 Olymp • {'🟢 CONNECTED' if assets else '🔴 WAITING'}\n📈 Assets discovered • {len(assets)}\n🕐 Real 1m candles • ON\n🧠 Market/Candle Brain • ON\n📊 Indicators • confirmation only\n🛡️ Read-only • YES\n🚫 Auto-trade • OFF\n🚫 Martingale • OFF")
    elif low.startswith('/performance'):
        try:
            from outcome_engine import performance
            p=performance(); _send(cid,f"📊 CANDICE PERFORMANCE\n\nSignals • {p['evaluated']}\nWins • {p['wins']}\nLosses • {p['losses']}\nWin rate • {p['win_rate']}%")
        except Exception:_send(cid,'📊 Performance is not ready yet.')

def _poller():
    global POLL_STARTED
    with LOCK:
        if POLL_STARTED:return
        POLL_STARTED=True
    if not TOKEN:return
    offset=0; LOG.info('Telegram single-owner poller started authorized=%s configured_chat=%s',AUTHORIZED,bool(CONFIGURED_CHAT))
    while True:
        try:
            r=_raw('GET','getUpdates',params={'timeout':25,'offset':offset+1,'allowed_updates':json.dumps(['message'])},timeout=35)
            if r is None:time.sleep(5);continue
            if r.status_code==409:time.sleep(10);continue
            if r.status_code==401:time.sleep(30);continue
            r.raise_for_status(); data=r.json()
            for u in data.get('result',[]):
                offset=max(offset,int(u.get('update_id',offset)))
                try:_handle_update(u)
                except Exception:LOG.exception('Telegram update handling failed')
        except Exception:time.sleep(5)

def _timer(chat,mid,asset,direction,entry,expiry,confidence):
    deadline=time.time()+expiry*60
    while time.time()<deadline:
        rem=max(0,int(deadline-time.time())); m,s=divmod(rem,60)
        try:_raw_post('editMessageText',{'chat_id':chat,'message_id':mid,'text':f'━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • LIVE MARKET\n━━━━━━━━━━━━━━━━━━━━\n🟢 SIGNAL • {direction}\n📈 Asset • {asset}\n💰 Entry • {entry:.6f}\n⏱️ Expiry • {expiry} min\n⏳ TIMER • {m:02d}:{s:02d}\n🧠 Confidence • {confidence}%\n📡 Source • Olymp Trade live market data\n🛡️ READ-ONLY / DEMO / MANUAL ONLY\n🚫 Auto-trade OFF • Martingale OFF','disable_web_page_preview':True})
        except Exception:pass
        time.sleep(5)
    try:_raw_post('editMessageText',{'chat_id':chat,'message_id':mid,'text':f'━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • EXPIRY\n━━━━━━━━━━━━━━━━━━━━\n📈 Asset • {asset}\n➡️ Direction • {direction}\n⏱️ Expiry • {expiry} min\n🏁 EXPIRY REACHED\n📊 Waiting for completed candle outcome…\n🛡️ READ-ONLY / DEMO / MANUAL ONLY'})
    except Exception:pass
    with LOCK:ACTIVE_SIGNALS.pop(asset,None)

def _telegram_request(self,method,url,**kwargs):
    if 'api.telegram.org' not in str(url):return ORIGINAL_REQUEST(self,method,url,**kwargs)
    is_send=str(url).endswith('/sendMessage')
    if is_send:
        p=kwargs.get('json')
        if isinstance(p,dict):
            p=dict(p); text=str(p.get('text','')); signal=('CANDICE AI' in text and 'SIGNAL' in text and 'Expiry' in text)
            with LOCK: auth=AUTHORIZED; target=ACTIVE_CHAT
            if signal and not auth:
                z=Response(); z.status_code=200; z._content=b'{"ok":true,"result":{"message_id":0}}'; return z
            if target:p['chat_id']=target
            kwargs['json']=p
    response=ORIGINAL_REQUEST(self,method,url,**kwargs)
    if is_send and response.ok:
        try:
            data=response.json(); text=str((kwargs.get('json') or {}).get('text','')); mid=(data.get('result') or {}).get('message_id')
            if AUTHORIZED and mid and 'SIGNAL' in text and 'Expiry' in text:
                am=re.search(r'Asset • ([^\n]+)',text); dm=re.search(r'SIGNAL • (UP|DOWN)',text); em=re.search(r'Expiry • (\d+) min',text); im=re.search(r'Entry • ([0-9.]+)',text); cm=re.search(r'Confidence • (\d+)%',text)
                if am and dm and em and im:
                    asset=am.group(1).strip(); expiry=int(em.group(1)); ACTIVE_SIGNALS[asset]=time.time()+expiry*60
                    threading.Thread(target=_timer,args=(ACTIVE_CHAT,int(mid),asset,dm.group(1),float(im.group(1)),expiry,int(cm.group(1)) if cm else 0),daemon=True).start()
        except Exception:LOG.exception('Telegram timer setup failed')
    return response
requests.sessions.Session.request=_telegram_request

def _scheduler(mod,original_send,outcome_register):
    """One signal maximum per five-minute window, selected from the preceding hour plus current window."""
    last=-1
    while True:
        now=int(time.time()); window=now//300; minute=now%300
        if window!=last:
            last=window
            with LOCK:SENT_WINDOWS.discard(window)
            LOG.info('SCAN_WINDOW window=%s prior_hour_windows=%s',window,','.join(str(window-i) for i in range(1,13)))
        with LOCK:
            auth=AUTHORIZED
            pool=[v for v in CANDIDATES.values() if window-12 <= v['window'] <= window]
            sent=(window in SENT_WINDOWS)
        if not auth:
            if minute==0:LOG.info('WINDOW_BLOCKED window=%s reason=telegram_not_authorized',window)
        elif not sent and pool:
            # Prefer the strongest recent setup. Current-window candidates are allowed to outrank stale candidates.
            best=max(pool,key=lambda x:(x['quality'],x['confidence'],1 if x['window']==window else 0,x.get('created',0)))
            expiry=int((best.get('brain') or best['tech'].get('brain') or {}).get('expiry',best['expiry']) or best['expiry'])
            if expiry not in SUPPORTED_EXPIRIES:expiry=5
            with LOCK:
                if window in SENT_WINDOWS:continue
                SENT_WINDOWS.add(window)
            asset,data,tech=best['asset'],best['data'],best['tech']
            try:
                result=original_send(asset,data,tech,expiry)
                if result.get('status')=='SIGNAL':
                    payload=dict(result); payload['timestamp']=data[-1].get('timestamp',time.time()); payload['entry']=data[-1].get('close'); b=tech.get('brain') or {}; payload['strategy']=b.get('strategy',''); payload['regime']=b.get('regime',''); payload['pattern']=b.get('pattern','')
                    try:outcome_register(payload)
                    except Exception:LOG.exception('Outcome registration failed')
                    LOG.info('WINDOW_SIGNAL window=%s minute=%s asset=%s quality=%s expiry=%s source_window=%s strategy=%s',window,minute,asset,best['quality'],expiry,best['window'],b.get('strategy'))
                else:
                    with LOCK:SENT_WINDOWS.discard(window)
                    LOG.info('WINDOW_REJECTED window=%s asset=%s reason=%s',window,asset,result.get('reason'))
            except Exception:
                with LOCK:SENT_WINDOWS.discard(window)
                LOG.exception('Window signal send failed')
        elif not sent and minute in (0,60,120,180,240):
            LOG.info('WINDOW_WAIT window=%s minute=%s candidates=%s',window,minute,len(pool))
        with LOCK:
            # Keep exactly one hour of history for ranking; older entries cannot affect new windows.
            for k,v in list(CANDIDATES.items()):
                if v['window']<window-12:CANDIDATES.pop(k,None)
        time.sleep(1)

def _install():
    while True:
        mod=sys.modules.get('__main__')
        if mod is not None and all(hasattr(mod,x) for x in ('analyze','on_olymp_candle','send_signal')):
            original_analyze,original_candle,original_send=mod.analyze,mod.on_olymp_candle,mod.send_signal
            try:
                from strategy_brain import evaluate as brain_evaluate
                from outcome_engine import on_candle as outcome_on_candle,performance as outcome_performance,register as outcome_register
            except Exception:LOG.exception('Analyst modules failed');time.sleep(1);continue
            def gated_candle(asset,candle):
                try:
                    result=original_candle(asset,candle)
                    try:outcome_on_candle(asset,candle,getattr(mod,'telegram',None))
                    except Exception:LOG.exception('Outcome evaluation failed')
                    return result
                except Exception:LOG.exception('Candle processing failed asset=%s',asset);return None
            def gated_analyze(data):
                asset=getattr(threading.current_thread(),'candice_asset',None) or getattr(getattr(mod,'live_feed',None),'current_asset',None) or os.getenv('CANDICE_CONTEXT_ASSET','') or 'UNKNOWN'
                result=original_analyze(data)
                try:brain=brain_evaluate(asset,data,result)
                except Exception:brain={'allow':False,'score':0,'strategy':'error','regime':'ERROR','reasons':['brain error']}
                enriched=dict(result or {}); enriched['brain']={'regime':brain.get('regime'),'strategy':brain.get('strategy'),'quality':brain.get('score',0),'pattern':brain.get('pattern'),'expiry':brain.get('expiry',5)}; enriched['reasons']=list(enriched.get('reasons',[]))[-4:]+list(brain.get('reasons',[]))[-5:]
                if not brain.get('allow'):enriched['decision']='NO_SIGNAL';return enriched
                bd=result.get('direction') if isinstance(result,dict) else None; dd=brain.get('direction')
                if bd and dd and bd!=dd:enriched['decision']='NO_SIGNAL';enriched['reasons']+=['technical direction disagrees with market brain'];return enriched
                enriched['decision']='SIGNAL'; enriched['direction']=dd or bd; enriched['confidence']=max(int(result.get('confidence',0) or 0),int(brain.get('score',0) or 0)); return enriched
            def wrapped_candle(asset,candle):
                threading.current_thread().candice_asset=str(asset)
                return gated_candle(asset,candle)
            def wrapped_send_signal(asset,data,tech,expiry=5):
                with LOCK:
                    if not AUTHORIZED:return {'status':'NO_SIGNAL','reason':'Telegram access not authorized'}
                    active=ACTIVE_SIGNALS.get(str(asset))
                    if active and active>time.time():return {'status':'NO_SIGNAL','reason':'active signal still within expiry'}
                if not isinstance(tech,dict) or tech.get('status')!='SIGNAL':return {'status':'NO_SIGNAL','reason':'not a qualified candidate'}
                brain=tech.get('brain') or {}; quality=int(brain.get('quality',0) or 0); window=int(time.time())//300
                adaptive=int(brain.get('expiry',expiry) or expiry); adaptive=adaptive if adaptive in SUPPORTED_EXPIRIES else 5
                key=f'{window}:{asset}'
                with LOCK:CANDIDATES[key]={'asset':asset,'data':list(data),'tech':dict(tech),'expiry':adaptive,'quality':quality,'confidence':int(tech.get('confidence',0) or 0),'window':window,'created':time.time()}
                return {'status':'SIGNAL','asset':asset,'direction':tech.get('direction'),'entry':data[-1].get('close'),'expiry':adaptive,'confidence':tech.get('confidence',0)}
            mod.analyze=gated_analyze; mod.on_olymp_candle=wrapped_candle; mod.send_signal=wrapped_send_signal
            if not hasattr(mod,'performance_api'):
                @mod.app.get('/performance')
                def performance_api():
                    try:p=outcome_performance()
                    except Exception:p={}
                    return mod.jsonify({'ok':True,'outcomes':p,'telegram_authorized':AUTHORIZED,'candidate_count':len(CANDIDATES)})
            threading.Thread(target=_poller,daemon=True,name='candice-telegram-owner').start()
            threading.Thread(target=_scheduler,args=(mod,original_send,outcome_register),daemon=True,name='candice-5m-scheduler').start()
            LOG.info('CANDICE 13.6 installed: prior-hour ranking + adaptive expiry + persistent Telegram authorization + read-only')
            return
        time.sleep(.25)
threading.Thread(target=_install,daemon=True,name='candice-analyst-stack').start()

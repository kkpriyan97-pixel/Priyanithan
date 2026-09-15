from __future__ import annotations
import json, logging, os, sqlite3, sys, threading, time
from pathlib import Path
log=logging.getLogger('candice.outcomes')
DB_PATH=Path(os.getenv('CANDICE_OUTCOME_DB','/tmp/candice_outcomes.sqlite3')); LOCK=threading.RLock()
WATCHDOG_STARTED=False

def _db():
    DB_PATH.parent.mkdir(parents=True,exist_ok=True); c=sqlite3.connect(str(DB_PATH),timeout=10)
    c.execute('''CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY AUTOINCREMENT,asset TEXT NOT NULL,direction TEXT NOT NULL,expiry INTEGER NOT NULL,entry_time REAL NOT NULL,entry_price REAL NOT NULL,confidence INTEGER DEFAULT 0,strategy TEXT DEFAULT '',regime TEXT DEFAULT '',status TEXT DEFAULT 'PENDING',exit_time REAL,exit_price REAL,result TEXT,created_at REAL NOT NULL)''')
    cols={r[1] for r in c.execute('PRAGMA table_info(signals)').fetchall()}
    for name,typ in [('pattern','TEXT DEFAULT ""'),('feature_json','TEXT DEFAULT "{}"'),('loss_reason','TEXT DEFAULT ""')]:
        if name not in cols:c.execute(f'ALTER TABLE signals ADD COLUMN {name} {typ}')
    c.commit(); return c

def register(signal):
    if not signal or signal.get('status')!='SIGNAL':return False
    entry_time=float(signal.get('entry_time', signal.get('timestamp',time.time())))
    row=(str(signal.get('asset','')).upper(),str(signal.get('direction','')),int(signal.get('expiry',5)),entry_time,float(signal.get('entry',0)),int(signal.get('confidence',0)),str(signal.get('strategy','')),str(signal.get('regime','')),str(signal.get('pattern','')),json.dumps(signal.get('features',{}),separators=(',',':')))
    with LOCK:
        c=_db(); exists=c.execute('SELECT id FROM signals WHERE asset=? AND direction=? AND expiry=? AND entry_time=?',row[:4]).fetchone()
        if exists:
            c.close(); return True
        c.execute('INSERT INTO signals(asset,direction,expiry,entry_time,entry_price,confidence,strategy,regime,pattern,feature_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',row+(time.time(),)); c.commit(); c.close()
        log.info('SIGNAL_REGISTERED asset=%s direction=%s expiry=%sm entry_time=%.3f entry=%s confidence=%s',row[0],row[1],row[2],entry_time,row[4],row[5])
    return True

def _result(direction,entry,exit_price):
    if direction=='UP':return 'WIN' if exit_price>entry else 'LOSS' if exit_price<entry else 'TIE'
    return 'WIN' if exit_price<entry else 'LOSS' if exit_price>entry else 'TIE'

def _loss_reason(direction,entry,exit_price,features):
    try:f=features if isinstance(features,dict) else json.loads(features or '{}')
    except Exception:f={}
    move=exit_price-entry
    if direction=='DOWN':move=-move
    if move<0:
        if f.get('pattern') in ('hammer_rejection','bullish_engulfing','shooting_star_rejection','bearish_engulfing'):return 'pattern_failed_or_reversed'
        if f.get('regime')=='TRANSITION':return 'market_transition'
        if f.get('volatility_spike'):return 'volatility_spike'
        return 'direction_failed_at_expiry'
    return ''

def _update_app_risk(result):
    try:
        mod=sys.modules.get('__main__'); risk=getattr(mod,'risk',None); reset=getattr(mod,'reset_risk',None)
        if not isinstance(risk,dict): return
        if callable(reset): reset()
        if result=='WIN':risk['wins']=int(risk.get('wins',0))+1;risk['streak']=0
        elif result=='LOSS':risk['losses_total']=int(risk.get('losses_total',0))+1;risk['losses']=int(risk.get('losses',0))+1;risk['streak']=int(risk.get('streak',0))+1
    except Exception:log.exception('Risk synchronization failed')

def on_candle(asset,candle,telegram=None):
    now=float(candle.get('timestamp',time.time())); close=float(candle.get('close')); completed=[]
    with LOCK:
        c=_db(); rows=c.execute('SELECT id,direction,expiry,entry_time,entry_price,confidence,strategy,regime,pattern,feature_json FROM signals WHERE asset=? AND status="PENDING" ORDER BY entry_time',(str(asset).upper(),)).fetchall()
        for sid,direction,expiry,entry_time,entry_price,confidence,strategy,regime,pattern,feature_json in rows:
            if now+0.1<entry_time+expiry*60:continue
            res=_result(direction,entry_price,close); reason=_loss_reason(direction,entry_price,close,feature_json)
            c.execute('UPDATE signals SET status="EVALUATED",exit_time=?,exit_price=?,result=?,loss_reason=? WHERE id=?',(now,close,res,reason,sid)); completed.append((sid,direction,expiry,entry_time,entry_price,close,res,confidence,strategy,regime,pattern,reason))
        c.commit();c.close()
    for row in completed:
        sid,direction,expiry,entry_time,entry,exit_price,res,confidence,strategy,regime,pattern,reason=row
        _update_app_risk(res)
        log.info('OUTCOME_EVALUATED id=%s asset=%s direction=%s expiry=%sm entry_time=%.3f entry=%s exit=%s result=%s strategy=%s regime=%s pattern=%s loss_reason=%s',sid,asset,direction,expiry,entry_time,entry,exit_price,res,strategy,regime,pattern,reason or '-')
        if telegram:
            icon='🟢🏆' if res=='WIN' else '🔴' if res=='LOSS' else '🟡'
            telegram(f'━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • TRADE RESULT\n━━━━━━━━━━━━━━━━━━━━\n{icon} {res}\n📈 Asset • {asset}\n➡️ Direction • {direction}\n⏱️ Expiry • {expiry} min\n💰 Entry • {entry:.6f}\n🏁 Exit • {exit_price:.6f}\n🧠 Confidence • {confidence}%\n🧩 Strategy • {strategy or "-"}\n🧬 Pattern • {pattern or "-"}\n🌐 Regime • {regime or "-"}\n🧠 Learning • {reason or "outcome stored"}\n📊 Risk • loss streak updated\n🛡️ READ-ONLY / DEMO / MANUAL ONLY')
    return completed

def _recover_sent_signals():
    """Recover signals delivered by the runtime overlay if its deferred send path did not register them."""
    try:
        sc=sys.modules.get('sitecustomize'); mod=sys.modules.get('__main__')
        if sc is None:return
        candidates=getattr(sc,'CANDIDATES',{}) or {}; sent_windows=getattr(sc,'SENT_WINDOWS',set()) or set(); last=getattr(sc,'LAST_SIGNAL',{}) or {}
        for asset,ctx in list(last.items()):
            sig=(ctx or {}).get('signal') or {}; w=int(float((ctx or {}).get('time',time.time()))//300)
            if not sig or w not in sent_windows:continue
            matches=[v for v in candidates.values() if v.get('window')==w and str(v.get('asset','')).upper()==str(asset).upper()]
            if not matches:continue
            cand=max(matches,key=lambda x:float(x.get('created',0)))
            b=cand.get('brain') or {}; expiry=int(cand.get('expiry',5) or 5); expiry=expiry if expiry in (2,3,5,15) else 5
            register({'status':'SIGNAL','asset':asset,'direction':cand.get('direction'),'expiry':expiry,'timestamp':float(sig.get('timestamp',time.time())),'entry':float(sig.get('close',0)),'confidence':int(cand.get('quality',0)),'strategy':b.get('strategy','market_brain'),'regime':b.get('regime',''),'pattern':b.get('pattern',''),'features':b})
    except Exception:log.exception('Sent-signal recovery failed')

def _watchdog():
    log.info('OUTCOME_WATCHDOG started | independent expiry/result processing ON')
    while True:
        try:
            _recover_sent_signals()
            mod=sys.modules.get('__main__'); candles=getattr(mod,'candles',{}) if mod else {}
            tg=getattr(mod,'telegram',None) if mod else None
            with LOCK:
                c=_db(); pending=c.execute('SELECT DISTINCT asset FROM signals WHERE status="PENDING"').fetchall(); c.close()
            for (asset,) in pending:
                data=list(candles.get(asset,[])) if hasattr(candles,'get') else []
                if not data:continue
                # Feed the newest completed candle into the normal evaluator. It is idempotent because DB status changes to EVALUATED.
                on_candle(asset,data[-1],tg)
            time.sleep(2)
        except Exception:log.exception('Outcome watchdog iteration failed');time.sleep(5)

def start_watchdog():
    global WATCHDOG_STARTED
    with LOCK:
        if WATCHDOG_STARTED:return
        WATCHDOG_STARTED=True
    threading.Thread(target=_watchdog,name='candice-outcome-watchdog',daemon=True).start()


def pattern_stats(asset=None):
    with LOCK:
        c=_db(); q='SELECT pattern,COUNT(*),SUM(result="WIN"),SUM(result="LOSS") FROM signals WHERE status="EVALUATED" AND pattern<>""'; args=[]
        if asset:q+=' AND asset=?';args.append(str(asset).upper())
        q+=' GROUP BY pattern ORDER BY COUNT(*) DESC'; rows=c.execute(q,args).fetchall();c.close()
    return [{'pattern':p,'trades':int(n),'wins':int(w or 0),'losses':int(l or 0),'win_rate':round(100*(w or 0)/((w or 0)+(l or 0)),1) if (w or 0)+(l or 0) else 0} for p,n,w,l in rows]

def learning_for(asset,pattern,regime=None):
    with LOCK:
        c=_db(); q='SELECT result FROM signals WHERE asset=? AND pattern=?';args=[str(asset).upper(),str(pattern)]
        if regime:q+=' AND regime=?';args.append(str(regime))
        q+=' AND status="EVALUATED" ORDER BY id DESC LIMIT 50';rows=c.execute(q,args).fetchall();c.close()
    w=sum(r[0]=='WIN' for r in rows);l=sum(r[0]=='LOSS' for r in rows);n=w+l
    return {'samples':n,'wins':w,'losses':l,'win_rate':round(100*w/n,1) if n else 0}

def performance():
    with LOCK:
        c=_db(); total,w,l,t=c.execute('SELECT COUNT(*),SUM(result="WIN"),SUM(result="LOSS"),SUM(result="TIE") FROM signals WHERE status="EVALUATED"').fetchone(); by_asset=c.execute('SELECT asset,COUNT(*),SUM(result="WIN"),SUM(result="LOSS") FROM signals WHERE status="EVALUATED" GROUP BY asset ORDER BY COUNT(*) DESC').fetchall();by_strategy=c.execute('SELECT strategy,COUNT(*),SUM(result="WIN"),SUM(result="LOSS") FROM signals WHERE status="EVALUATED" GROUP BY strategy ORDER BY COUNT(*) DESC').fetchall();c.close()
    total=int(total or 0);w=int(w or 0);l=int(l or 0);t=int(t or 0);wr=round(100*w/(w+l),1) if w+l else 0
    return {'evaluated':total,'wins':w,'losses':l,'ties':t,'win_rate':wr,'by_asset':[{'asset':a,'trades':int(n),'wins':int(x or 0),'losses':int(y or 0)} for a,n,x,y in by_asset],'by_strategy':[{'strategy':s or '-', 'trades':int(n),'wins':int(x or 0),'losses':int(y or 0)} for s,n,x,y in by_strategy],'patterns':pattern_stats()}

start_watchdog()

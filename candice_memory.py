from __future__ import annotations
import json, os, threading
from pathlib import Path
from datetime import datetime, timezone
PATH=Path(os.getenv('CANDICE_MEMORY_FILE','data/candice_memory.json')); LOCK=threading.Lock(); MAX=5000

def _load():
    try:
        x=json.loads(PATH.read_text()); return x if isinstance(x,list) else []
    except Exception:return []
def record(event):
    PATH.parent.mkdir(parents=True,exist_ok=True)
    with LOCK:
        x=_load(); x.append(event); PATH.write_text(json.dumps(x[-MAX:],ensure_ascii=False))
def recent(asset=None,n=12):
    x=_load();
    if asset: x=[e for e in x if e.get('asset')==asset]
    return x[-n:]
def summary(asset=None):
    x=recent(asset,100); results=[e.get('result') for e in x if e.get('result') in ('WIN','LOSS')]; w=results.count('WIN'); l=results.count('LOSS')
    return {'samples':len(results),'wins':w,'losses':l,'win_rate':round(100*w/len(results),1) if results else None,'recent':x[-8:]}
def today_results(tz=None):
    tz=tz or timezone.utc; day=datetime.now(tz).date(); out=[]
    for e in _load():
        if e.get('result') not in ('WIN','LOSS'): continue
        try:
            if datetime.fromtimestamp(float(e.get('ts',0)),tz).date()==day: out.append(e)
        except Exception: pass
    return out
def today_risk(tz=None):
    x=today_results(tz); losses=sum(e.get('result')=='LOSS' for e in x); streak=0
    for e in reversed(x):
        if e.get('result')=='LOSS': streak+=1
        else: break
    return {'losses':losses,'streak':streak,'results':len(x)}

def authorized_users():
    """Load authorized Telegram user IDs from the persistent memory file."""
    users=set()
    for e in _load():
        if e.get('type') == 'authorized_user':
            try: users.add(int(e.get('user_id')))
            except Exception: pass
    return users

def authorize_user(user_id):
    """Persist a Telegram user authorization without storing access codes."""
    uid=int(user_id)
    if uid not in authorized_users():
        record({'ts': datetime.now(timezone.utc).timestamp(), 'type':'authorized_user', 'user_id':uid})
    return uid

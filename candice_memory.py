from __future__ import annotations
import json, os, threading
from pathlib import Path

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
    x=recent(asset,100); results=[e.get('result') for e in x if e.get('result') in ('WIN','LOSS')]
    w=results.count('WIN'); l=results.count('LOSS'); return {'samples':len(results),'wins':w,'losses':l,'win_rate':round(100*w/len(results),1) if results else None,'recent':x[-8:]}

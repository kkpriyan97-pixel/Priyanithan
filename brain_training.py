"""Candice Brain training/ranking layer: research only, never places trades."""
from __future__ import annotations
import json, os, sqlite3, time
from collections import defaultdict
DB_PATH=os.getenv('CANDICE_OUTCOME_DB','/tmp/candice_outcomes.sqlite3')
METHODS=('trend_following','breakout','pullback','support_resistance','candlestick','momentum','mean_reversion','reversal','multi_timeframe','volatility','market_structure','price_action')

def _rows(limit=500):
    try:
        c=sqlite3.connect(DB_PATH,timeout=2)
        rows=c.execute('SELECT asset,direction,expiry,confidence,strategy,regime,pattern,result,feature_json FROM signals WHERE status="EVALUATED" ORDER BY id DESC LIMIT ?', (limit,)).fetchall();c.close();return rows
    except Exception:return []

def train_snapshot():
    rows=_rows(); by=defaultdict(lambda:[0,0,0])
    for asset,direction,expiry,conf,strategy,regime,pattern,result,features in rows:
        k=(str(asset).upper(),str(direction),int(expiry),str(regime),str(pattern))
        by[k][0]+=1;by[k][1]+=result=='WIN';by[k][2]+=result=='LOSS'
    ranked=[]
    for k,(n,w,l) in by.items():
        if n<2:continue
        ranked.append({'key':k,'samples':n,'wins':w,'losses':l,'win_rate':round(100*w/(w+l),1) if w+l else 0})
    ranked.sort(key=lambda x:(x['win_rate'],x['samples']),reverse=True)
    return {'evaluated':len(rows),'methods':list(METHODS),'ranked_setups':ranked[:50],'trained_at':time.time()}

def score_setup(asset,direction,expiry,regime,pattern):
    s=train_snapshot()
    for x in s['ranked_setups']:
        if x['key']==(str(asset).upper(),str(direction),int(expiry),str(regime),str(pattern)):
            return x['win_rate'],x['samples']
    return 50.0,0

if __name__=='__main__':
    print(json.dumps(train_snapshot(),separators=(',',':')))

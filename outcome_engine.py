from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path

log = logging.getLogger("candice.outcomes")
DB_PATH = Path(os.getenv("CANDICE_OUTCOME_DB", "/tmp/candice_outcomes.sqlite3"))
_LOCK = threading.RLock()
_PENDING = deque(maxlen=5000)
_STATS = {"wins": 0, "losses": 0, "ties": 0, "evaluated": 0}


def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=10)
    con.execute("""CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset TEXT NOT NULL, direction TEXT NOT NULL, expiry INTEGER NOT NULL,
        entry_time REAL NOT NULL, entry_price REAL NOT NULL,
        confidence INTEGER DEFAULT 0, strategy TEXT DEFAULT '', regime TEXT DEFAULT '',
        status TEXT DEFAULT 'PENDING', exit_time REAL, exit_price REAL,
        result TEXT, created_at REAL NOT NULL
    )""")
    con.commit()
    return con


def register(signal: dict):
    if not signal or signal.get("status") != "SIGNAL":
        return
    row = (
        str(signal.get("asset", "")).upper(), str(signal.get("direction", "")),
        int(signal.get("expiry", 5)), float(signal.get("timestamp", time.time())),
        float(signal.get("entry", 0)), int(signal.get("confidence", 0)),
        str(signal.get("strategy", "")), str(signal.get("regime", "")),
    )
    with _LOCK:
        con = _db()
        cur = con.execute("SELECT id FROM signals WHERE asset=? AND direction=? AND expiry=? AND entry_time=?", row[:4])
        if cur.fetchone() is None:
            con.execute("INSERT INTO signals(asset,direction,expiry,entry_time,entry_price,confidence,strategy,regime,created_at) VALUES(?,?,?,?,?,?,?,?,?)", row + (time.time(),))
            con.commit()
            _PENDING.append(row)
        con.close()


def _result(direction: str, entry: float, exit_price: float):
    if direction == "UP":
        return "WIN" if exit_price > entry else "LOSS" if exit_price < entry else "TIE"
    return "WIN" if exit_price < entry else "LOSS" if exit_price > entry else "TIE"


def on_candle(asset: str, candle: dict, telegram=None):
    now = float(candle.get("timestamp", time.time()))
    close = float(candle.get("close"))
    completed = []
    with _LOCK:
        con = _db()
        rows = con.execute("SELECT id,direction,expiry,entry_time,entry_price,confidence,strategy,regime FROM signals WHERE asset=? AND status='PENDING' ORDER BY entry_time", (str(asset).upper(),)).fetchall()
        for row in rows:
            sid, direction, expiry, entry_time, entry_price, confidence, strategy, regime = row
            target = entry_time + expiry * 60
            if now + 0.1 < target:
                continue
            res = _result(direction, entry_price, close)
            con.execute("UPDATE signals SET status='EVALUATED',exit_time=?,exit_price=?,result=? WHERE id=?", (now, close, res, sid))
            _STATS["evaluated"] += 1
            _STATS[res.lower() + "s"] += 1
            completed.append((sid, direction, expiry, entry_price, close, res, confidence, strategy, regime))
        con.commit()
        con.close()
    for sid, direction, expiry, entry, exit_price, res, confidence, strategy, regime in completed:
        log.info("OUTCOME asset=%s direction=%s expiry=%sm entry=%s exit=%s result=%s strategy=%s regime=%s", asset, direction, expiry, entry, exit_price, res, strategy, regime)
        if telegram:
            icon = "🟢🏆" if res == "WIN" else "🔴" if res == "LOSS" else "🟡"
            telegram("━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • TRADE RESULT\n━━━━━━━━━━━━━━━━━━━━\n" f"{icon} {res}\n📈 Asset • {asset}\n➡️ Direction • {direction}\n⏱️ Expiry • {expiry} min\n💰 Entry • {entry:.6f}\n🏁 Exit • {exit_price:.6f}\n🧠 Confidence • {confidence}%\n🧩 Strategy • {strategy or '-'}\n🌐 Regime • {regime or '-'}\n🛡️ READ-ONLY / DEMO / MANUAL ONLY")
    return completed


def performance():
    with _LOCK:
        con = _db()
        total, wins, losses, ties = con.execute("SELECT COUNT(*),SUM(result='WIN'),SUM(result='LOSS'),SUM(result='TIE') FROM signals WHERE status='EVALUATED'").fetchone()
        by_asset = con.execute("SELECT asset,COUNT(*),SUM(result='WIN'),SUM(result='LOSS') FROM signals WHERE status='EVALUATED' GROUP BY asset ORDER BY COUNT(*) DESC").fetchall()
        by_strategy = con.execute("SELECT strategy,COUNT(*),SUM(result='WIN'),SUM(result='LOSS') FROM signals WHERE status='EVALUATED' GROUP BY strategy ORDER BY COUNT(*) DESC").fetchall()
        con.close()
    total = int(total or 0); wins = int(wins or 0); losses = int(losses or 0); ties = int(ties or 0)
    wr = round(100 * wins / (wins + losses), 1) if wins + losses else 0.0
    return {"evaluated": total, "wins": wins, "losses": losses, "ties": ties, "win_rate": wr,
            "by_asset": [{"asset": a, "trades": int(n), "wins": int(w or 0), "losses": int(l or 0)} for a,n,w,l in by_asset],
            "by_strategy": [{"strategy": s or "-", "trades": int(n), "wins": int(w or 0), "losses": int(l or 0)} for s,n,w,l in by_strategy]}

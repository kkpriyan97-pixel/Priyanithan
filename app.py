from __future__ import annotations

import logging, os, threading, time
from collections import deque
from flask import Flask, jsonify
from olymp_live import OlympLiveFeed
from candice_brain import CandiceBrain

os.environ["TZ"]="Asia/Dubai"
try: time.tzset()
except AttributeError: pass
logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"),format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log=logging.getLogger("candice")
app=Flask(__name__)
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","").strip();CHAT_ID=os.getenv("TELEGRAM_CHAT_ID","").strip();candles={};lock=threading.RLock();brain=CandiceBrain();live_feed=None;active_chat=CHAT_ID;poll_started=False

def telegram_send(text):return brain.telegram_send(text,active_chat)

def telegram_poll():
    global active_chat,poll_started
    if poll_started or not TOKEN:return
    poll_started=True;offset=0
    import requests
    log.info("TELEGRAM_POLL started | token_only=%s",not bool(CHAT_ID))
    while True:
        try:
            r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",params={"timeout":20,"offset":offset+1,"allowed_updates":["message"]},timeout=30)
            if r.status_code in (409,429):time.sleep(5);continue
            r.raise_for_status()
            for u in r.json().get("result",[]):
                offset=max(offset,int(u.get("update_id",offset)));m=u.get("message") or {};sender=m.get("from") or {};cid=str((m.get("chat") or {}).get("id",""));text=str(m.get("text","")).strip()
                if sender.get("is_bot") or not cid:continue
                if text.lower().startswith("/start"):
                    active_chat=cid;telegram_send("🎯 CANDICE AI\n\n✅ ONLINE\n📡 LIVE MARKET • READ-ONLY\n🚫 AUTO-TRADE • OFF\n🚫 MARTINGALE • OFF");log.info("TELEGRAM_CHAT_AUTHORIZED chat=%s",cid)
        except Exception as e:log.warning("Telegram poll error: %s",type(e).__name__);time.sleep(5)

def on_history(asset,candle):
    with lock:
        q=candles.setdefault(asset,deque(maxlen=360));ts=float(candle.get("timestamp",0))
        if not ts:return
        rows=[x for x in q if float(x.get("timestamp",-1))!=ts];rows.append(dict(candle));rows.sort(key=lambda x:float(x.get("timestamp",0)));q.clear();q.extend(rows[-360:])

def on_candle(asset,candle):
    on_history(asset,candle);brain.on_completed_candle(asset,candle,candles,live_feed,telegram_send)

def brain_boot():
    log.info("CANDICE_BRAIN_WAITING_FOR_HISTORY | direct live feed | target=60 completed 1m candles per asset")
    deadline=time.time()+90
    while time.time()<deadline:
        with lock:
            ready=sum(1 for q in candles.values() if len(q)>=60);assets=len(candles)
        if ready>=1:
            log.info("CANDICE_HISTORY_READY | assets_with_60m=%s candle_assets=%s",ready,assets);break
        time.sleep(2)
    brain.loop(candles,lambda:live_feed,telegram_send)

def start():
    global live_feed
    if live_feed is not None:return
    live_feed=OlympLiveFeed(on_candle=on_candle,on_history=on_history);live_feed.start()
    threading.Thread(target=brain_boot,daemon=True,name="candice-brain").start();threading.Thread(target=telegram_poll,daemon=True,name="candice-telegram").start()
    log.info("CANDICE CLEAN ENGINE started | timezone=Asia/Dubai UTC+04:00 | direct live feed | read-only")

@app.get("/")
def root():return jsonify({"name":"Candice AI","status":"online","mode":"READ_ONLY","auto_trade":False,"martingale":False,"timezone":"Asia/Dubai"})
@app.get("/status")
def status():
    s=live_feed.status() if live_feed else {}
    return jsonify({"candice":"online","broker_connected":bool(s.get("connected")),"assets":len(s.get("assets") or []),"candles":{a:len(v) for a,v in candles.items()},"account_mode":s.get("account_mode","UNKNOWN"),"account_balance":s.get("account_balance"),"account_currency":s.get("account_currency",""),"brain":brain.status(),"timezone":"Asia/Dubai"})

if __name__=="__main__":start();app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))

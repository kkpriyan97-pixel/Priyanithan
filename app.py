from __future__ import annotations
import asyncio,logging,os,threading,time
from flask import Flask,jsonify
from market_feed import LiveMarketFeed
from brain import CandiceBrain
from telegram import Telegram
os.environ["TZ"]="Asia/Dubai"
try:time.tzset()
except AttributeError:pass
logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"),format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log=logging.getLogger("candice")
app=Flask(__name__)
telegram=Telegram();feed=None;brain=None
def start_engine():
 global feed,brain
 async def runner():
  global feed,brain
  feed=LiveMarketFeed(lambda a,c:brain.on_candle(a,c) if brain else None)
  await feed.start()
 def run():asyncio.run(runner())
 threading.Thread(target=run,daemon=True,name="candice-live-feed").start()
 while feed is None or not feed.connected:time.sleep(1)
 brain=CandiceBrain(telegram.send,feed)
 threading.Thread(target=brain.run,daemon=True,name="candice-brain").start()
 log.info("CANDICE_ENGINE_STARTED | Dubai UTC+04:00 | READ_ONLY | AUTO_TRADE=OFF | MARTINGALE=OFF")
@app.get("/")
def root():return jsonify({"name":"Candice AI","status":"online","mode":"READ_ONLY","auto_trade":False,"martingale":False,"timezone":"Asia/Dubai"})
@app.get("/health")
def health():
 fs=feed.status() if feed else {"connected":False}
 ok=bool(fs.get("connected"))
 return jsonify({"status":"ok" if ok else "degraded","feed_connected":ok,"mode":"READ_ONLY","auto_trade":False,"martingale":False}),200 if ok else 503
@app.get("/status")
def status():
 fs=feed.status() if feed else {};bs={"pending":0,"WIN":0,"LOSS":0,"TIE":0}
 if brain:bs={"pending":len([x for x in brain.pending.values() if not x.get("result")]),"WIN":brain.stats["WIN"],"LOSS":brain.stats["LOSS"],"TIE":brain.stats["TIE"]}
 return jsonify({"candice":"online","feed":fs,"brain":bs,"timezone":"Asia/Dubai"})
if __name__=="__main__":
 threading.Thread(target=start_engine,daemon=True).start();app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))

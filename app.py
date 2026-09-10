import asyncio
import io
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands

from olymptrade_ws import OlympTradeClient
from olymptrade_ws.olympconfig import parameters

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ACCESS_CODE = os.getenv("ACCESS_CODE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
OLYMPTRADE_ACCESS_TOKEN = os.getenv("OLYMPTRADE_ACCESS_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
AIRFORCE_API_KEY = os.getenv("AIRFORCE_API_KEY")
AIRFORCE_MODEL = os.getenv("AIRFORCE_MODEL", "gpt-oss-120b")
AI_MIN_CONFIDENCE = int(os.getenv("AI_MIN_CONFIDENCE", "60"))
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))
LIVE_UPDATE_SECONDS = 15
AUTO_TRADE = False
MARTINGALE = False
PAIR_ENV = os.getenv("OLYMP_PAIRS", "AUTO")
MANUAL_PAIRS = [x.strip().upper() for x in PAIR_ENV.split(",") if x.strip() and x.strip().upper() != "AUTO"]
PAIRS = MANUAL_PAIRS[:] if MANUAL_PAIRS else []
AUTO_DISCOVER_ASSETS = os.getenv("AUTO_DISCOVER_ASSETS", "true").lower() in ("1", "true", "yes", "on")
MAX_ASSETS_PER_CYCLE = int(os.getenv("MAX_ASSETS_PER_CYCLE", "120"))
MAX_AI_CANDIDATES = min(int(os.getenv("MAX_AI_CANDIDATES", "2")), 2)
MAX_SIGNALS_PER_CYCLE = int(os.getenv("MAX_SIGNALS_PER_CYCLE", "3"))
PAIR_ALIASES = {"ASIA_X": os.getenv("OT_ASIA_X_PAIR", "ASIA_X"), "EURUSD": os.getenv("OT_EURUSD_PAIR", "EURUSD"), "GBPUSD": os.getenv("OT_GBPUSD_PAIR", "GBPUSD")}
UAE_TZ = ZoneInfo("Asia/Dubai")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("priyanithan")
APP_VERSION = "6.0-FINAL-LIVE-UAE-WEBHOOK"
app = Flask(__name__)
authorized_users = set(); known_chat_ids = set(); ot_client = None; runtime_loop = None
latest_candles = {}; latest_ticks = {}; latest_signal = {}; manual_trades = {}; discovered_assets = {}; state_lock = threading.Lock()
@app.get("/")
def home(): return f"Priyanithan AI OlympTrade Signal Bot is ONLINE — {APP_VERSION}"
@app.get("/health")
def health(): return "OK"
telegram_application = None; manual_scan_task = None
@app.post("/telegram/webhook")
def telegram_webhook():
    if telegram_application is None or runtime_loop is None: return "Bot is starting", 503
    payload=request.get_json(silent=True)
    if not isinstance(payload,dict): return "Bad Request",400
    try:
        update=Update.de_json(payload,telegram_application.bot); future=asyncio.run_coroutine_threadsafe(telegram_application.update_queue.put(update),runtime_loop); future.result(timeout=5); return "OK",200
    except Exception as e: log.warning("Telegram webhook update failed: %s",e); return "Webhook processing failed",500
def now_uae(): return datetime.now(UAE_TZ)
def format_uae_timestamp(ts):
    try: return datetime.fromtimestamp(float(ts),tz=timezone.utc).astimezone(UAE_TZ).strftime("%H:%M:%S UAE")
    except Exception: return now_uae().strftime("%H:%M:%S UAE")
async def wait_until_next_5min_uae():
    now=now_uae(); next_minute=((now.minute//5)+1)*5
    target=(now+timedelta(hours=1)).replace(minute=0,second=0,microsecond=0) if next_minute>=60 else now.replace(minute=next_minute,second=0,microsecond=0)
    await asyncio.sleep(max(1,(target-now).total_seconds()))
def remember_chat(update):
    chat=getattr(update,"effective_chat",None)
    if chat and getattr(chat,"id",None) is not None: known_chat_ids.add(int(chat.id))
def is_authorized(update): user=getattr(update,"effective_user",None); return bool(user and user.id in authorized_users)
def recipients():
    ids=set(authorized_users)
    if TELEGRAM_CHAT_ID:
        try: ids.add(int(TELEGRAM_CHAT_ID))
        except ValueError: pass
    return list(ids)
async def send_to_recipients(bot,text):
    ids=recipients()
    if not ids: return False
    sent=False
    for chat_id in ids:
        try: await bot.send_message(chat_id=chat_id,text=text); sent=True
        except Exception as e: log.warning("Telegram send failed chat_id=%s: %s",chat_id,e)
    return sent
def normalize_candles(raw):
    if isinstance(raw,dict):
        d=raw.get("d"); raw=d[0].get("candles",d) if isinstance(d,list) and d and isinstance(d[0],dict) else d
    if not isinstance(raw,list): return None
    rows=[]
    for c in raw:
        if not isinstance(c,dict): continue
        o,h,l,cl=[c.get(k,c.get(k[0])) for k in ("open","high","low","close")]; ts=c.get("timestamp",c.get("t",c.get("time")))
        if None in (o,h,l,cl): continue
        try: rows.append({"timestamp":float(ts) if ts is not None else time.time(),"open":float(o),"high":float(h),"low":float(l),"close":float(cl),"volume":float(c.get("volume",c.get("v",0)) or 0)})
        except: continue
    return pd.DataFrame(rows).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True) if rows else None
async def on_tick(message):
    data=message.get("d",[]) if isinstance(message,dict) else []
    if isinstance(data,list):
        for item in data:
            if isinstance(item,dict):
                pair=str(item.get("pair") or item.get("symbol") or "").upper()
                if pair: latest_ticks[pair]=item
async def on_balance(message): pass
def extract_instruments(message):
    data=message.get("d") if isinstance(message,dict) else None
    if isinstance(data,dict): data=data.get("instruments",data.get("pairs",data.get("items",[])))
    found={}
    if isinstance(data,list):
        for item in data:
            if isinstance(item,dict):
                pair=str(item.get("pair") or item.get("symbol") or item.get("name") or item.get("id") or "").upper().strip()
                if pair: found[pair]=item
    return found
async def on_instruments(message):
    found=extract_instruments(message)
    if found:
        discovered_assets.update(found)
        if AUTO_DISCOVER_ASSETS: PAIRS[:]=sorted(discovered_assets.keys())[:MAX_ASSETS_PER_CYCLE]
async def on_trade_event(message): pass
async def olymptrade_connect_loop():
    global ot_client,PAIRS
    if not OLYMPTRADE_ACCESS_TOKEN: log.error("OLYMPTRADE_ACCESS_TOKEN is not configured."); return
    while True:
        try:
            client=OlympTradeClient(access_token=OLYMPTRADE_ACCESS_TOKEN,log_raw_messages=False); client.register_callback(parameters.E_TICK_UPDATE,on_tick); client.register_callback(parameters.E_BALANCE_UPDATE,on_balance); client.register_callback(1054,on_instruments); await client.start(); ot_client=client
            while client.connection.is_connected: await asyncio.sleep(5)
            raise ConnectionError("OlympTrade WebSocket disconnected")
        except asyncio.CancelledError: raise
        except Exception as e: log.error("OlympTrade connection error: %s",e); ot_client=None; await asyncio.sleep(5)
async def get_ot_candles(pair,timeframe=60,count=120):
    if ot_client is None: return None,"OlympTrade client unavailable"
    try: raw=await ot_client.get_candles(pair,timeframe,count)
    except Exception as e: return None,f"candle request failed: {e}"
    df=normalize_candles(raw)
    if df is None or len(df)<40: return None,f"insufficient candle data rows={0 if df is None else len(df)}"
    latest_ts=float(df["timestamp"].iloc[-1])
    if latest_ts>100000000000: latest_ts/=1000; df["timestamp"]/=1000
    age=time.time()-latest_ts
    if age>180: return None,f"STALE OlympTrade candles rejected ({age:.1f}s old)"
    return df,None
def analyze_pair(pair,df):
    close,high,low=df["close"],df["high"],df["low"]; ema9=EMAIndicator(close,9).ema_indicator(); ema21=EMAIndicator(close,21).ema_indicator(); macd_obj=MACD(close); macd=macd_obj.macd(); ms=macd_obj.macd_signal(); rsi=RSIIndicator(close,14).rsi(); stoch=StochasticOscillator(high,low,close).stoch(); adx=ADXIndicator(high,low,close).adx(); bb=BollingerBands(close); bh=bb.bollinger_hband(); bl=bb.bollinger_lband(); c=float(close.iloc[-1]); prev=float(close.iloc[-2]); e9=float(ema9.iloc[-1]); e21=float(ema21.iloc[-1]); m=float(macd.iloc[-1]); msv=float(ms.iloc[-1]); rv=float(rsi.iloc[-1]); sv=float(stoch.iloc[-1]); av=float(adx.iloc[-1]); score_up=(2 if e9>e21 else 0)+(2 if m>msv else 0)+(1 if 52<=rv<70 else 0)+(1 if c<bl else 0)+(1 if sv<20 else 0); score_down=(2 if e9<e21 else 0)+(2 if m<msv else 0)+(1 if 30<rv<=48 else 0)+(1 if c>bh else 0)+(1 if sv>80 else 0); signal="UP" if score_up>score_down else ("DOWN" if score_down>score_up else "NO SIGNAL"); conf=int(min(99,50+abs(score_up-score_down)*7+max(0,av-20))); return {"pair":pair,"signal":signal,"confidence":conf,"candle_time":format_uae_timestamp(float(df["timestamp"].iloc[-1])),"patterns":["bullish close" if c>prev else "bearish close"],"trend":"BULLISH" if score_up>score_down else ("BEARISH" if score_down>score_up else "MIXED"),"rsi":rv,"adx":av,"reason":"technical structure","price":c}
def choose_duration_min(result,ai):
    try: requested=int(ai.get("duration_min",5)) if isinstance(ai,dict) else 5
    except: requested=5
    return requested if requested in [1,2,3,5,10,15] else 5
def ai_prompt(result): return json.dumps(result)
def call_ai(prompt): return None,"AI provider unavailable"
def normalize_ai_decision(ai): return ai
def format_signal(result,ai=None):
    if not ai: return "🚫 NO SIGNAL"
    return f"🔥 PRIYANITHAN AI SIGNAL 🔥\n\n📈 {result['pair']}\n{'⬆️' if ai.get('direction')=='UP' else '⬇️'} {ai.get('direction')}\n💰 Entry: {result['price']}\n⏱️ Expiry: {choose_duration_min(result,ai)} MIN\n🤖 AI Confidence: {ai.get('confidence',0)}%\n\n⚠️ MANUAL TRADE — AUTO TRADE OFF"
async def scan_cycle(application):
    universe=sorted(discovered_assets.keys())[:MAX_ASSETS_PER_CYCLE] if AUTO_DISCOVER_ASSETS else (MANUAL_PAIRS[:] if MANUAL_PAIRS else PAIRS[:MAX_ASSETS_PER_CYCLE]); candidates=[]
    for pair in universe:
        df,err=await get_ot_candles(pair,60,120)
        if df is None: continue
        try: result=analyze_pair(pair,df)
        except: continue
        if result["signal"]!="NO SIGNAL": candidates.append(result)
    candidates.sort(key=lambda x:x["confidence"],reverse=True)
    for result in candidates[:MAX_AI_CANDIDATES]:
        ai,err=call_ai(ai_prompt(result))
        if ai and ai.get("decision")=="APPROVE" and ai.get("direction")==result["signal"] and int(ai.get("confidence",0))>=AI_MIN_CONFIDENCE:
            await send_to_recipients(application.bot,format_signal(result,ai)); return
    await send_to_recipients(application.bot,"🚫 NO QUALIFIED SIGNAL")
async def access_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    remember_chat(update)
    if not ACCESS_CODE: await update.message.reply_text("ACCESS_CODE is not configured."); return
    args=context.args or []
    if args and args[0].strip()==ACCESS_CODE:
        user=getattr(update,"effective_user",None)
        if user: authorized_users.add(int(user.id)); await update.message.reply_text("✅ Access approved. This chat can receive signals.")
        return
    await update.message.reply_text("❌ Invalid access code.")
async def start_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    global manual_scan_task
    remember_chat(update)
    if not is_authorized(update): await update.message.reply_text("Priyanithan AI is online. Use /access YOUR_CODE first; then /start again to begin a live scan."); return
    if manual_scan_task is not None and not manual_scan_task.done(): await update.message.reply_text("⏳ Live scan is already running. Please wait for the result."); return
    await update.message.reply_text("⚡ LIVE SCAN STARTED — analysing fresh market data now..."); manual_scan_task=asyncio.create_task(scan_cycle(context.application),name="manual-scan")
async def scan_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    remember_chat(update)
    if not is_authorized(update): await update.message.reply_text("❌ Not authorized. Use /access YOUR_CODE first."); return
    global manual_scan_task
    if manual_scan_task is not None and not manual_scan_task.done(): await update.message.reply_text("⏳ Live scan is already running. Please wait for the result."); return
    await update.message.reply_text("🔎 Live scan started — analysing fresh market data now..."); manual_scan_task=asyncio.create_task(scan_cycle(context.application),name="manual-scan")
async def scan_loop(application):
    await wait_until_next_5min_uae()
    while True:
        try: await scan_cycle(application)
        except asyncio.CancelledError: raise
        except Exception: log.exception("Scanner loop error")
        await wait_until_next_5min_uae()
async def manual_trade_monitor(application):
    while True:
        await asyncio.sleep(LIVE_UPDATE_SECONDS)
async def telegram_runtime(application):
    global runtime_loop,telegram_application
    runtime_loop=asyncio.get_running_loop(); telegram_application=application; await application.initialize(); await application.start(); tasks=[asyncio.create_task(olymptrade_connect_loop()),asyncio.create_task(scan_loop(application)),asyncio.create_task(manual_trade_monitor(application))]
    try:
        webhook_base=os.getenv("RENDER_EXTERNAL_URL","https://priyanithan-ai.onrender.com").rstrip("/"); await application.bot.set_webhook(url=f"{webhook_base}/telegram/webhook",drop_pending_updates=True,allowed_updates=["message","callback_query"]); await asyncio.Event().wait()
    finally:
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True); await application.stop(); await application.shutdown()
def main():
    global ot_client
    if not TELEGRAM_BOT_TOKEN: raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    application=Application.builder().token(TELEGRAM_BOT_TOKEN).updater(None).build(); application.add_handler(CommandHandler("start",start_cmd)); application.add_handler(CommandHandler("access",access_cmd)); application.add_handler(CommandHandler("scan",scan_cmd)); ot_client=OlympTradeClient(OLYMPTRADE_ACCESS_TOKEN,parameters); asyncio.run(telegram_runtime(application))
def start_web_server():
    port=int(os.getenv("PORT","10000")); app.run(host="0.0.0.0",port=port,debug=False,use_reloader=False)
if __name__=="__main__":
    # Register all runtime Flask routes before the web server can accept its
    # first request. Flask 3.x rejects add_url_rule after first request.
    try:
        import fast_manual_entry
        fast_manual_entry.install()
    except Exception:
        log.exception("FAST MANUAL ENTRY STARTUP INSTALL FAILED")
    threading.Thread(target=start_web_server,daemon=True).start(); main()
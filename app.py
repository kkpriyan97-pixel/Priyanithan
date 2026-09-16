from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from flask import Flask, jsonify, request, Response

from brain import CandiceBrain
import brain_fix
import asset_discovery_fix
from market_feed import LiveMarketFeed
from telegram import Telegram

os.environ["TZ"]="Asia/Dubai"
try: time.tzset()
except AttributeError: pass
logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"),format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log=logging.getLogger("candice")
app=Flask(__name__); telegram=Telegram(); feed=None; brain=None; engine_ready=False; engine_error=None
brain_fix.apply(); asset_discovery_fix.apply()

def status_payload():
    fs=feed.status() if feed else {"connected":False,"auth_invalid":False,"assets":[],"subscribed":0,"ticks":0,"completed_1m":0,"history_1m":{},"account_mode":"UNKNOWN","account_balance":None,"account_currency":"","account_snapshots":{"demo":None,"real":None}}
    bs={"pending":0,"WIN":0,"LOSS":0,"TIE":0,"last_scan_assets":[],"daily_losses":0,"consecutive_losses":0,"daily_loss_limit":0}
    if brain:
        bs={"pending":len([x for x in brain.pending.values() if not x.get("result")]),"WIN":brain.stats["WIN"],"LOSS":brain.stats["LOSS"],"TIE":brain.stats["TIE"],"last_scan_assets":sorted(brain.last_scan),"daily_losses":brain.daily_losses,"consecutive_losses":brain.consecutive_losses,"daily_loss_limit":brain.max_daily_losses}
    return {"candice":"online" if engine_ready else "starting","engine_ready":engine_ready,"engine_error":engine_error,"feed":fs,"brain":bs,"timezone":"Asia/Dubai","mode":"READ_ONLY","auto_trade":False,"martingale":False}

def _telegram_report():
    x=status_payload(); f=x.get("feed") or {}; b=x.get("brain") or {}; snaps=f.get("account_snapshots") or {}; assets=f.get("assets") or []
    client=getattr(feed,"client",None) if feed else None
    account_ids=getattr(client,"account_ids",[]) if client else []
    trader_ids=getattr(client,"trader_ids",[]) if client else []
    account_records=getattr(client,"account_records",[]) if client else []
    def fmt(v):
        if v is None:return "—"
        if isinstance(v,(list,tuple,set)):return ", ".join(map(str,v)) if v else "—"
        if isinstance(v,dict):return "; ".join(f"{k}={v}" for k,v in v.items() if v is not None) or "—"
        return str(v)
    return "\n".join([
        "🔐 CANDICE AI • FULL ACCESS REPORT","━━━━━━━━━━━━━━━━━━━━",
        f"⚙️ Engine • {'ONLINE' if x.get('engine_ready') else 'STARTING/DEGRADED'}",
        f"📡 Feed • {'CONNECTED' if f.get('connected') else 'NOT CONNECTED'}",
        f"🧠 Brain • {'RUNNING' if x.get('engine_ready') else 'NOT READY'}","",
        "👤 OLYMPTRADE ACCOUNT",f"• Mode • {f.get('account_mode','UNKNOWN')}",f"• Demo • {fmt(snaps.get('demo'))}",f"• Real • {fmt(snaps.get('real'))}",f"• Selected balance • {fmt(f.get('account_balance'))} {f.get('account_currency','')}",f"• Trader ID(s) • {fmt(trader_ids)}",f"• Account ID(s) • {fmt(account_ids)}",f"• Account records • {len(account_records)}","",
        "🟢 FLEX MARKET","• Flex-only enforcement • ON",f"• Discovered • {len(assets)}",f"• Subscribed • {f.get('subscribed',0)}",f"• Assets • {fmt(assets)}","• Forex processing • BLOCKED","",
        "📊 MARKET ENGINE",f"• Ticks • {f.get('ticks',0)}",f"• Completed 1m candles • {f.get('completed_1m',0)}","• Read-only market data • ON","",
        "🧠 BRAIN / RESULTS",f"• Pending • {b.get('pending',0)}",f"• WIN • {b.get('WIN',0)} | LOSS • {b.get('LOSS',0)} | TIE • {b.get('TIE',0)}",f"• Consecutive losses • {b.get('consecutive_losses',0)}",f"• Daily losses • {b.get('daily_losses',0)}/{b.get('daily_loss_limit',0)}",f"• Last scan • {fmt(b.get('last_scan_assets'))}","",
        "🔒 SAFETY","• Read-only • ON","• Auto-trade • OFF","• Martingale • OFF","• Login/password/browser-cookie extraction • OFF"
    ])

def install_telegram_status_commands():
    original=telegram._handle_message
    def handler(message):
        chat=message.get("chat") or {}; chat_id=chat.get("id"); text=(message.get("text") or "").strip()
        if chat_id is None or not text:return original(message)
        chat_id=str(chat_id)
        if text.startswith("/access"):
            parts=text.split(maxsplit=1); supplied=parts[1].strip() if len(parts)==2 else ""
            if telegram.access_code and supplied==telegram.access_code:
                telegram.chat=chat_id; telegram.authorized=True; log.info("TELEGRAM_ACCESS_GRANTED")
                telegram._send_to(chat_id,_telegram_report()); return
            return original(message)
        if text in ("/status","/report","/account","/assets"):
            if telegram.authorized and telegram.chat==chat_id: telegram._send_to(chat_id,_telegram_report())
            else: telegram._send_to(chat_id,"🔐 Access required.\nUse: /access <your access code>")
            return
        return original(message)
    telegram._handle_message=handler

install_telegram_status_commands()

async def _engine_loop():
    global feed,brain,engine_ready,engine_error
    try:
        brain_ref=None
        feed_ref=LiveMarketFeed(lambda a,c: brain_ref.on_candle(a,c) if brain_ref else None)
        brain_ref=CandiceBrain(telegram.send,feed_ref); feed=feed_ref; brain=brain_ref
        log.info("CANDICE_STARTING | timezone=Asia/Dubai | READ_ONLY")
        telegram.start(); await feed.start(); engine_ready=True; engine_error=None
        log.info("CANDICE_ENGINE_STARTED | Dubai UTC+04:00 | READ_ONLY | AUTO_TRADE=OFF | MARTINGALE=OFF")
        threading.Thread(target=brain.run,daemon=True,name="candice-brain").start(); log.info("CANDICE_BRAIN_STARTED")
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        engine_ready=False; raise
    except Exception as e:
        engine_ready=False; engine_error=type(e).__name__; log.exception("CANDICE_ENGINE_START_FAILED")

def start_engine(): asyncio.run(_engine_loop())

@app.get("/")
def root():
    return Response('''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Candice AI</title><style>body{font-family:system-ui;background:#080b12;color:#f4f6fb;margin:0;padding:24px}main{max-width:760px;margin:auto}section{background:#101521;border:1px solid #263043;border-radius:18px;padding:18px;margin:14px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.box{background:#0b101a;border-radius:12px;padding:12px}.value{font-size:24px;font-weight:700;margin-top:5px}.ok{color:#5ee6a8}.warn{color:#ffd45c}code{word-break:break-word}</style></head><body><main><h1>🎯 CANDICE AI <span id="live" class="muted">• STARTING</span></h1><div class="muted">Live market intelligence • read-only / manual only</div><section><h2 id="system">SYSTEM</h2><div class="grid"><div class="box">ACCOUNT MODE<div id="mode" class="value">—</div></div><div class="box">BALANCE<div id="balance" class="value">—</div></div><div class="box">ASSETS DISCOVERED<div id="assets" class="value">0</div></div><div class="box">TRADEABLE / SUBSCRIBED<div id="sub" class="value">0 / 0</div></div><div class="box">TICKS<div id="ticks" class="value">—</div></div><div class="box">COMPLETED 1M<div id="candles" class="value">—</div></div></div></section><section><h2>SIGNAL ENGINE</h2><div id="signal" class="value">Waiting for qualified setup...</div><div class="muted">1m live candles • next 5m decision window</div><p>Scanned assets: <code id="scan">—</code></p></section><section><h2>RISK / PERFORMANCE</h2><div id="perf" class="value">0 WIN / 0 LOSS</div><div class="muted" id="risk">Signals 0 • Current loss streak 0 • Daily losses 0</div></section><section><h2>VERIFICATION</h2><div id="verify" class="muted">Connecting...</div></section></main><script>async function refresh(){try{let r=await fetch('/status?ts='+Date.now(),{cache:'no-store'}),x=await r.json(),f=x.feed||{},b=x.brain||{};document.getElementById('live').textContent=x.engine_ready?'• LIVE':'• STARTING';document.getElementById('system').textContent=x.engine_ready?'SYSTEM • ONLINE':'SYSTEM • STARTING';document.getElementById('mode').textContent=f.account_mode||'UNKNOWN';document.getElementById('balance').textContent=f.account_balance==null?'NOT AVAILABLE':(f.account_balance+' '+(f.account_currency||''));let a=f.assets||[];document.getElementById('assets').textContent=a.length;document.getElementById('sub').textContent=(f.subscribed||0)+' / '+a.length;document.getElementById('ticks').textContent=f.ticks??'—';document.getElementById('candles').textContent=f.completed_1m??'—';let scan=b.last_scan_assets||[];document.getElementById('scan').textContent=scan.length?scan.join(', '):'No qualified setups yet';document.getElementById('signal').textContent=scan.length?'Brain actively analysing '+scan.length+' asset(s)':'Waiting for qualified setup...';document.getElementById('perf').textContent=(b.WIN||0)+' WIN / '+(b.LOSS||0)+' LOSS';document.getElementById('risk').textContent='Signals '+((b.WIN||0)+(b.LOSS||0)+(b.TIE||0))+' • Current loss streak '+(b.consecutive_losses||0)+' • Daily losses '+(b.daily_losses||0)+'/'+(b.daily_loss_limit||0);document.getElementById('verify').textContent=(f.connected?'✓ Market feed connected':'⚠ Market feed not connected')+' • '+(a.length?'Assets discovered: '+a.length:'Waiting for asset discovery')+' • READ_ONLY • Auto-trade OFF • Martingale OFF';}catch(e){document.getElementById('verify').textContent='⚠ Status unavailable'}}refresh();setInterval(refresh,2000)</script></body></html>''',mimetype='text/html')

@app.get("/health")
def health():
    fs=feed.status() if feed else {"connected":False,"auth_invalid":False}; ok=bool(engine_ready and fs.get("connected")); return jsonify({"status":"ok" if ok else "degraded","feed_connected":bool(fs.get("connected")),"brain_started":brain is not None,"engine_ready":engine_ready,"mode":"READ_ONLY","auto_trade":False,"martingale":False,"timezone":"Asia/Dubai","error":engine_error}),200 if ok else 503

@app.get("/status")
def status(): return jsonify(status_payload())

@app.post("/telegram/webhook")
def telegram_webhook():
    try: telegram.handle_webhook(request.get_json(silent=True) or {}); return jsonify({"ok":True})
    except Exception: log.exception("TELEGRAM_WEBHOOK_HANDLER_FAILED"); return jsonify({"ok":False}),500

if __name__=="__main__":
    threading.Thread(target=start_engine,daemon=True,name="candice-engine-bootstrap").start()
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))

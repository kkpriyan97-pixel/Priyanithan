from __future__ import annotations
import asyncio, logging, os, threading, time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler
from candice_broker import Broker
from candice_engine import analyze, session_state, technical_snapshot
from candice_memory import record, summary, today_risk

VERSION='8.0-CLEAN-TEXT'; UAE=ZoneInfo('Asia/Dubai')
TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','').strip(); ACCESS=os.getenv('ACCESS_CODE','').strip(); OT_TOKEN=os.getenv('OLYMPIATRADE_ACCESS_TOKEN', os.getenv('OLYMPTRADE_ACCESS_TOKEN','')).strip()
INTERVAL=max(60,int(os.getenv('SCAN_INTERVAL_SECONDS','300'))); MAX_DAILY_LOSSES=max(1,int(os.getenv('DAILY_MAX_LOSSES','5'))); MAX_STREAK=max(1,int(os.getenv('MAX_CONSECUTIVE_LOSSES','3')))
AUTO_TRADE=False; MARTINGALE=False; FOREX_MODE=False
logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),format='%(asctime)s %(levelname)s %(name)s: %(message)s'); log=logging.getLogger('candice')
flask_app=Flask(__name__); broker=Broker(OT_TOKEN); tg_app=None
users=set(); selected={}; active={}; sent_keys=set(); recovery_until={}; daily={'date':'','losses':0,'streak':0}
@dataclass
class Signal: asset:str; direction:str; confidence:int; expiry:int; entry:float; entry_ts:float; reason:str
def now(): return datetime.now(UAE)
def reset_daily():
 daily['date']=now().date().isoformat(); r=today_risk(UAE); daily['losses']=int(r.get('losses',0)); daily['streak']=int(r.get('streak',0))
def session_window():
 u=datetime.now(timezone.utc); base=u.replace(minute=0,second=0,microsecond=0)-timedelta(hours=u.hour%3); se=base+timedelta(hours=2); be=base+timedelta(hours=3)
 return (*([base,se,be,True] if u<se else [se,be,be,False]),)
def session_payload():
 s,e,n,a=session_window(); return {'active':a,'session_start':s.astimezone(UAE).strftime('%H:%M:%S UAE'),'session_end':e.astimezone(UAE).strftime('%H:%M:%S UAE'),'next_session':n.astimezone(UAE).strftime('%H:%M:%S UAE'),'remaining':max(0,int((e.astimezone(UAE)-now()).total_seconds()))}
def header(title): return f'━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • LIVE MARKET\n━━━━━━━━━━━━━━━━━━━━\n✨ {title}\n━━━━━━━━━━━━━━━━━━━━'
def keyboard(assets): return InlineKeyboardMarkup([[InlineKeyboardButton(('🟢 '+a),callback_data=f'asset:{a}') for a in assets[i:i+2]] for i in range(0,len(assets),2)] or [[InlineKeyboardButton('⚠️ No live FLEX assets',callback_data='noop')]])
async def send_text(cid,text,reply_markup=None):
 if tg_app is None:return None
 try:
  m=await tg_app.bot.send_message(chat_id=cid,text=text,reply_markup=reply_markup,disable_web_page_preview=True,read_timeout=30,write_timeout=30,connect_timeout=15,pool_timeout=15); log.info('TELEGRAM TEXT SENT chat=%s message_id=%s',cid,m.message_id); return m
 except Exception as e: log.exception('TELEGRAM TEXT FAILED: %s',e); return None
async def edit_text(cid,mid,text,reply_markup=None):
 try: await tg_app.bot.edit_message_text(chat_id=cid,message_id=mid,text=text,reply_markup=reply_markup,disable_web_page_preview=True); return True
 except Exception as e: log.warning('TELEGRAM TEXT EDIT FAILED message_id=%s: %s',mid,e); return False
async def cmd_access(update,ctx):
 if not ACCESS or not ctx.args or ctx.args[0].strip()!=ACCESS: await update.message.reply_text('🔒 CANDICE AI\n\nAccess denied.'); return
 users.add(update.effective_user.id); await cmd_assets(update,ctx)
async def cmd_start(update,ctx):
 if update.effective_user.id not in users: await update.message.reply_text('🔒 CANDICE AI\n\nUse /access <code> first.'); return
 await cmd_assets(update,ctx)
async def cmd_assets(update,ctx):
 assets=await broker.live_assets(); names=' • '.join(assets) if assets else 'No fresh FLEX assets available'
 text=(f'{header("FLEX ASSET SELECTOR")}\n\n'
       f'📈 LIVE MARKET\n🟢 Fresh 1-minute candle VERIFIED\n\n'
       f'🔥 Available: {len(assets)}\n{names}\n\n'
       '⚡ FLEX / FIXED-TIME\n🧠 Candice AI research ACTIVE\n🛡️ Technical + AI confirmation required\n\n'
       '👇 Select your asset')
 await send_text(update.effective_user.id,text,reply_markup=keyboard(assets))
async def cmd_status(update,ctx):
 reset_daily(); uid=update.effective_user.id
 await send_text(uid,f'{header("SYSTEM STATUS")}\n\n🛰️ Broker: {"🟢 CONNECTED" if broker.connected() else "🔴 DISCONNECTED"}\n📈 FLEX: {selected.get(uid,"NONE")}\n🧠 Session: {session_state()}\n❌ Daily losses: {daily["losses"]}/{MAX_DAILY_LOSSES}\n🔥 Loss streak: {daily["streak"]}/{MAX_STREAK}\n\n🚫 Auto-trade OFF\n🚫 Martingale OFF\n🚫 Forex mode OFF')
async def cmd_session(update,ctx):
 p=session_payload(); mode='🟢 SIGNAL SESSION' if p['active'] else '🔵 RESEARCH ONLY'
 await send_text(update.effective_user.id,f'{header("SESSION CONTROL")}\n\n{mode}\n⏳ Ends: {p["session_end"]}\n⏱️ Remaining: {p["remaining"]} sec\n\n🔬 Research: 24/7\n📡 Signal scan: every 5 minutes\n⚡ FLEX / Fixed-Time only')
async def cmd_update(update,ctx): await cmd_session(update,ctx)
async def asset_callback(update,ctx):
 q=update.callback_query; await q.answer(); uid=q.from_user.id
 if uid not in users or not q.data.startswith('asset:'): return
 asset=q.data.split(':',1)[1].upper()
 if asset=='NOOP': return
 live=await broker.live_assets()
 if asset not in live:
  await edit_text(uid,q.message.message_id,f'{header("ASSET CHECK FAILED")}\n\n🔴 {asset} is no longer live or its candle is stale.\n\nUse /start again.')
  return
 selected[uid]=asset; t=now().strftime('%H:%M:%S UAE')
 await edit_text(uid,q.message.message_id,
  f'{header("ASSET READY")}\n\n'
  f'📈 {asset}\n\n'
  '🟢 MARKET STATUS • LIVE\n'
  '🔵 CANDLE • 1 MIN FRESH\n'
  f'🕒 VERIFIED • {t}\n\n'
  '🔥🔥🔥 CANDICE RESEARCH ACTIVE\n\n'
  '🟢 Trend structure\n'
  '🔵 Momentum confirmation\n'
  '🟣 Volatility / breakout check\n'
  '🟠 MACD confirmation\n'
  '🟡 AI decision gate\n\n'
  '⚡ READY FOR QUALIFIED SIGNAL\n'
  '⏱️ 2 / 3 / 5 / 10 / 15 MIN\n\n'
  '🛡️ MANUAL TRADE ONLY • AUTO-TRADE OFF')
async def signal_countdown(cid,msg,s):
 if msg is None:return
 end=s.entry_ts+s.expiry*60
 while time.time()<end and active.get(cid) is s:
  rem=max(0,int(end-time.time())); mm,ss=divmod(rem,60)
  text=(f'{header("ACTIVE SIGNAL")}\n\n📈 {s.asset}\n'
        f'🟢 {"⬆️ TRADE UP" if s.direction=="UP" else "🔴 TRADE DOWN"}\n'
        f'🔥 Confidence • {s.confidence}%\n⏱️ Duration • {s.expiry} MIN\n💰 Entry • {s.entry:.6f}\n\n'
        f'⏳ COUNTDOWN • {mm:02d}:{ss:02d}\n🕒 Expiry • {datetime.fromtimestamp(end,UAE).strftime("%H:%M:%S UAE")}\n\n'
        '🟢 Technical gate PASSED\n🟣 AI gate APPROVED\n🚫 Auto-trade OFF • Manual only')
  await edit_text(cid,msg.message_id,text); await asyncio.sleep(min(30,max(1,rem)))
async def result_monitor(uid,s,msg):
 end=s.entry_ts+s.expiry*60; await signal_countdown(uid,msg,s)
 await send_text(uid,f'{header("EXPIRY VERIFICATION")}\n\n📈 {s.asset}\n➡️ Direction • {s.direction}\n💰 Entry • {s.entry:.6f}\n🕒 Boundary • {datetime.fromtimestamp(end,UAE).strftime("%H:%M:%S UAE")}\n\n🔎 Reading closed candle...')
 price=None; err=''
 for _ in range(5):
  df,err=await broker.candles(s.asset,60,20,120)
  if df is not None and not df.empty: price=float(df.close.iloc[-1]); break
  await asyncio.sleep(3)
 if price is None:
  active.pop(uid,None); await send_text(uid,f'{header("RESULT UNRESOLVED")}\n\n📈 {s.asset}\n💰 Entry • {s.entry:.6f}\n❔ Exit • —\n⚠️ {err or "No fresh expiry price"}'); return
 result='DRAW' if price==s.entry else ('WIN' if ((s.direction=='UP' and price>s.entry) or (s.direction=='DOWN' and price<s.entry)) else 'LOSS')
 record({'ts':time.time(),'asset':s.asset,'direction':s.direction,'confidence':s.confidence,'expiry':s.expiry,'entry':s.entry,'exit':price,'result':result}); reset_daily(); active.pop(uid,None)
 icon={'WIN':'🟢🏆','LOSS':'🔴⚠️','DRAW':'🟡➖'}[result]
 await send_text(uid,f'{header("FINAL MARKET OUTCOME")}\n\n{icon} {result}\n\n📈 {s.asset}\n➡️ {s.direction}\n💰 Entry • {s.entry:.6f}\n🏁 Expiry • {price:.6f}\n⏱️ Duration • {s.expiry} MIN\n🕒 {datetime.fromtimestamp(end,UAE).strftime("%H:%M:%S UAE")}\n\n🔎 Verification • CANDLE-CLOSED\n❌ Daily losses • {daily["losses"]}/{MAX_DAILY_LOSSES}\n🔥 Loss streak • {daily["streak"]}/{MAX_STREAK}\n\nMARKET OUTCOME • NOT BROKER ACCOUNT P/L\n🚫 AUTO-TRADE OFF • MANUAL ONLY')
 if result=='LOSS':
  recovery_until[uid]=time.time()+300
  await send_text(uid,f'{header("RECOVERY WINDOW")}\n\n🛡️ Loss protection ACTIVE\n⏸️ Next signal paused • 5 MIN\n🔬 Research continues\n⏱️ 15 MIN context • 5 MIN recovery review\n🚫 Martingale OFF • Auto-trade OFF')
async def scan_once():
 reset_daily(); assets=await broker.live_assets(); log.info('CANDICE SCAN START assets=%d session=%s users=%d',len(assets),session_state(),len(users)); sem=asyncio.Semaphore(6)
 async def research(asset):
  async with sem:
   df,e=await broker.candles(asset,60,60,360)
   if e is None and df is not None:
    s=technical_snapshot(df); record({'ts':time.time(),'asset':asset,'research':True,'direction':s['direction'],'strength':s['strength'],'rsi':s['rsi14'],'adx':s['adx14']}); log.info('CANDICE RESEARCH pair=%s direction=%s strength=%.2f RSI=%.1f ADX=%.1f',asset,s['direction'],s['strength'],s['rsi14'],s['adx14'])
   else: log.info('CANDICE RESEARCH REJECT pair=%s reason=%s',asset,e or 'no fresh candles')
 await asyncio.gather(*(research(a) for a in assets[:30]))
 if session_state()!='SIGNAL' or not users or daily['losses']>=MAX_DAILY_LOSSES or daily['streak']>=MAX_STREAK:return
 for uid,asset in list(selected.items()):
  if uid in active or asset not in assets: continue
  if recovery_until.get(uid,0)>time.time(): continue
  recovery_until.pop(uid,None); a=await analyze(asset,broker,summary(asset)); log.info('AI ANALYSIS pair=%s decision=%s direction=%s confidence=%s expiry=%s reason=%s',asset,a.decision,a.direction,a.confidence,a.expiry,a.reason)
  if a.decision!='APPROVE': continue
  key=f'{uid}:{asset}:{int(time.time()//300)}'
  if key in sent_keys: continue
  df,e=await broker.candles(asset,60,20,120)
  if e or df is None or df.empty: continue
  entry=float(df.close.iloc[-1]); ts=time.time(); s=Signal(asset,a.direction,a.confidence,a.expiry,entry,ts,a.reason); active[uid]=s; sent_keys.add(key); end=ts+a.expiry*60
  text=(f'{header("NEW SIGNAL")}\n\n📈 {asset}\n\n'
        f'{"🟢 ⬆️ TRADE UP" if a.direction=="UP" else "🔴 ⬇️ TRADE DOWN"}\n'
        f'🕒 {datetime.fromtimestamp(ts,UAE).strftime("%H:%M")} (GMT+4)\n\n🔥🔥🔥 STRONG CONFIRMATION\n\n'
        '🟢 Parabolic SAR Reversal\n🔵 Moving Average Crossover\n🟣 Donchian Channel Breakout\n🟠 MACD Crossover\n🟡 Rate of Change Crossover\n\n'
        f'⏱️ {a.expiry}m\n🧠 AI Confidence • {a.confidence}%\n\n⚠️ MANUAL TRADE ONLY • AUTO-TRADE OFF')
  msg=await send_text(uid,text); asyncio.create_task(result_monitor(uid,s,msg))
async def scheduler():
 while True:
  await asyncio.sleep(max(1,INTERVAL-(time.time()%INTERVAL)))
  try: await scan_once()
  except Exception: log.exception('scan cycle failed')
async def bot_main():
 global tg_app
 if not TOKEN: raise RuntimeError('TELEGRAM_BOT_TOKEN is missing')
 tg_app=Application.builder().token(TOKEN).build()
 for command,fn in [('start',cmd_start),('access',cmd_access),('assets',cmd_assets),('status',cmd_status),('session',cmd_session),('update',cmd_update)]: tg_app.add_handler(CommandHandler(command,fn))
 tg_app.add_handler(CallbackQueryHandler(asset_callback,r'^asset:')); await tg_app.initialize(); await tg_app.bot.delete_webhook(drop_pending_updates=True); await tg_app.start(); await tg_app.updater.start_polling(drop_pending_updates=True); log.warning('CANDICE TELEGRAM ONLINE — COLOR TEXT RUNTIME'); asyncio.create_task(broker.connect_forever()); asyncio.create_task(scheduler())
 while True: await asyncio.sleep(3600)
@flask_app.get('/')
def home(): return f'{VERSION} ONLINE — FLEX market-data / colorful text-only manual signals'
@flask_app.get('/health')
def health(): return 'OK'
@flask_app.get('/status')
def web_status(): return {'version':VERSION,'broker_connected':broker.connected(),'selected_users':len(selected),'auto_trade':False,'martingale':False,'forex_mode':False,'flex_mode':True,'telegram_media':False}
def http(): flask_app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')),threaded=True,use_reloader=False)
if __name__=='__main__': threading.Thread(target=http,daemon=True).start(); asyncio.run(bot_main())

from __future__ import annotations
import asyncio, logging, os, threading, time, io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, InputMediaPhoto, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler
from candice_broker import Broker
from candice_engine import analyze, session_state, technical_snapshot
from candice_memory import record, summary, today_risk
from candice_ui import png_bytes
logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),format='%(asctime)s %(levelname)s %(name)s: %(message)s'); log=logging.getLogger('candice')
VERSION='8.0-FRESH-CANDICE'; UAE=ZoneInfo('Asia/Dubai')
TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','').strip(); ACCESS=os.getenv('ACCESS_CODE','').strip(); OT_TOKEN=os.getenv('OLYMPTRADE_ACCESS_TOKEN','').strip()
INTERVAL=max(60,int(os.getenv('SCAN_INTERVAL_SECONDS','300'))); MAX_DAILY_LOSSES=max(1,int(os.getenv('DAILY_MAX_LOSSES','5'))); MAX_STREAK=max(1,int(os.getenv('MAX_CONSECUTIVE_LOSSES','3')))
AUTO_TRADE=False; MARTINGALE=False; FOREX_MODE=False
flask_app=Flask(__name__); broker=Broker(OT_TOKEN); tg_app=None
users=set(); selected={}; active={}; sent_keys=set(); recovery_until={}; timer_tasks={}; daily={'date':'','losses':0,'streak':0}
def now(): return datetime.now(UAE)
def reset_daily():
 d=now().date().isoformat(); daily['date']=d; r=today_risk(UAE); daily['losses']=r['losses']; daily['streak']=r['streak']
def session_window():
 u=datetime.now(timezone.utc); base=u.replace(minute=0,second=0,microsecond=0)-timedelta(hours=u.hour%3); signal_end=base+timedelta(hours=2); block_end=base+timedelta(hours=3)
 if u<signal_end: start,end=base,signal_end; active_now=True
 else: start,end=signal_end,block_end; active_now=False
 return start.astimezone(UAE),end.astimezone(UAE),block_end.astimezone(UAE),active_now
def session_payload():
 start,end,next_start,active_now=session_window(); return dict(active=active_now,session_start=start.strftime('%Y-%m-%d %H:%M:%S UAE'),session_end=end.strftime('%Y-%m-%d %H:%M:%S UAE'),next_session=next_start.strftime('%Y-%m-%d %H:%M:%S UAE'),remaining=max(0,int((end-now()).total_seconds())),total_seconds=max(1,int((end-start).total_seconds())))
async def send_image(cid,kind='selected',asset='ASIA_X',direction='',confidence=0,expiry=0,reason='',reply_markup=None,caption='',**kwargs):
 if tg_app is None: log.warning('TELEGRAM IMAGE SKIPPED kind=%s asset=%s',kind,asset); return None
 try:
  payload=png_bytes(kind=kind,asset=asset,direction=direction,confidence=confidence,expiry=expiry,reason=reason,**kwargs); payload.seek(0); payload.name='candice-update.png'
  msg=await tg_app.bot.send_photo(chat_id=cid,photo=InputFile(payload,filename='candice-update.png'),caption=caption or 'CANDICE AI • Live Market • Manual Trade Only',reply_markup=reply_markup,read_timeout=30,write_timeout=30,connect_timeout=15,pool_timeout=15)
  log.info('TELEGRAM IMAGE SENT kind=%s asset=%s message_id=%s',kind,asset,msg.message_id); return msg
 except Exception as e: log.exception('TELEGRAM IMAGE FAILED kind=%s asset=%s: %s',kind,asset,e); return None
async def edit_image(cid,message_id,kind,asset='ASIA_X',direction='',confidence=0,expiry=0,reason='',caption='',**kwargs):
 try:
  payload=png_bytes(kind=kind,asset=asset,direction=direction,confidence=confidence,expiry=expiry,reason=reason,**kwargs); payload.seek(0); payload.name='candice-update.png'
  await tg_app.bot.edit_message_media(chat_id=cid,message_id=message_id,media=InputMediaPhoto(media=payload,filename='candice-update.png',caption=caption or 'CANDICE AI • Live Market • Manual Trade Only')); return True
 except Exception as e: log.warning('TELEGRAM IMAGE TIMER EDIT FAILED kind=%s message_id=%s: %s',kind,message_id,e); return False
async def signal_timer(cid,msg,signal):
 if msg is None:return
 total=signal.expiry*60; end=signal.entry_ts+total
 while time.time()<end:
  rem=int(end-time.time()); await asyncio.sleep(min(30,max(1,rem)))
  rem=int(end-time.time())
  if rem<=0: break
  await edit_image(cid,msg.message_id,'signal',signal.asset,signal.direction,signal.confidence,signal.expiry,signal.reason,entry=f'{signal.entry:.6f}',technical='PASSED',signal_time=datetime.fromtimestamp(signal.entry_ts,UAE).strftime('%H:%M:%S UAE'),total_seconds=total,remaining=rem,expiry_time=datetime.fromtimestamp(end,UAE).strftime('%H:%M:%S UAE'))
  log.info('CANDICE SIGNAL TIMER asset=%s remaining=%s',signal.asset,rem)
async def session_timer(cid,msg):
 if msg is None:return
 while True:
  p=session_payload(); await asyncio.sleep(30)
  if not await edit_image(cid,msg.message_id,'session',selected.get(cid,'SYSTEM'),**session_payload()): break
async def recovery_timer(cid,msg,asset):
 if msg is None:return
 end=recovery_until.get(cid,time.time()); total=300
 while time.time()<end:
  rem=int(end-time.time()); await asyncio.sleep(min(30,max(1,rem))); rem=int(end-time.time())
  if rem<=0: break
  if not await edit_image(cid,msg.message_id,'recovery',asset,recovery='15 MIN',quick_recovery='5 MIN',remaining=rem,total_seconds=total,next_signal='Automatic after recovery',daily_limit=f'{daily["losses"]}/{MAX_DAILY_LOSSES}'): break
async def send_session_image(cid):
 p=session_payload(); msg=await send_image(cid,'session',selected.get(cid,'SYSTEM'),**p)
 if msg: timer_tasks[f'session:{cid}']=asyncio.create_task(session_timer(cid,msg))
 return msg
def keyboard(assets):
 return InlineKeyboardMarkup([[InlineKeyboardButton(a,callback_data=f'asset:{a}') for a in assets[i:i+2]] for i in range(0,len(assets),2)] or [[InlineKeyboardButton('No live FLEX assets',callback_data='noop')]])
async def cmd_access(update,ctx):
 if not ACCESS or not ctx.args or ctx.args[0].strip()!=ACCESS: await update.message.reply_text('🔒 CANDICE AI\n\nAccess denied.'); return
 users.add(update.effective_user.id); await cmd_assets(update,ctx)
async def cmd_start(update,ctx):
 if update.effective_user.id not in users: await update.message.reply_text('🔒 CANDICE AI\n\nUse /access <code> first.'); return
 await cmd_assets(update,ctx)
async def cmd_update(update,ctx):
 if update.effective_user.id not in users: await update.message.reply_text('🔒 CANDICE AI\n\nUse /access <code> first.'); return
 await send_session_image(update.effective_user.id)
async def cmd_assets(update,ctx):
 assets=await broker.live_assets(); return await send_image(update.effective_user.id,'selected','FLEX',reply_markup=keyboard(assets),caption='CANDICE AI • FLEX ASSET SELECTOR • Manual Trade Only',selected_time=now().strftime('%H:%M:%S UAE'))
async def cmd_status(update,ctx):
 reset_daily(); await send_image(update.effective_user.id,'status',selected.get(update.effective_user.id,'SYSTEM'),status_text=f'Broker: {"CONNECTED" if broker.connected() else "DISCONNECTED"} • Session: {session_state()} • Daily losses: {daily["losses"]}/{MAX_DAILY_LOSSES}')
async def cmd_session(update,ctx): await send_session_image(update.effective_user.id)
async def asset_callback(update,ctx):
 q=update.callback_query; await q.answer(); uid=q.from_user.id
 if uid not in users or not q.data.startswith('asset:'): return
 asset=q.data.split(':',1)[1].upper(); live=await broker.live_assets()
 if asset not in live:
  try: await q.edit_message_caption(caption='⚠️ CANDICE AI • ASSET NO LONGER LIVE\n\nPlease use /start again.')
  except Exception: pass
  return
 selected[uid]=asset; selected_at=now().strftime('%H:%M:%S UAE')
 try: await q.edit_message_caption(caption=f'🎯 CANDICE AI • ASSET SELECTED — {asset}\n🟢 Fresh 1-minute candle verified')
 except Exception: pass
 await send_image(uid,'selected',asset,selected_time=selected_at)
@dataclass
class Signal:
 asset:str; direction:str; confidence:int; expiry:int; entry:float; entry_ts:float; reason:str
async def result_monitor(uid,signal,msg):
 end=signal.entry_ts+signal.expiry*60; await signal_timer(uid,msg,signal)
 await send_image(uid,'expiry',signal.asset,direction=signal.direction,expiry=signal.expiry,entry=f'{signal.entry:.6f}',expiry_time=datetime.fromtimestamp(end,UAE).strftime('%H:%M:%S UAE'),reached_time=now().strftime('%H:%M:%S UAE'))
 price=None; err=''
 for _ in range(5):
  df,e=await broker.candles(signal.asset,60,20,120); err=e or ''
  if df is not None and not df.empty: price=float(df.close.iloc[-1]); break
  await asyncio.sleep(3)
 if price is None:
  await send_image(uid,'result',signal.asset,direction='UNRESOLVED',expiry=signal.expiry,entry=f'{signal.entry:.6f}',exit='—',verification='No fresh expiry candle',result_time=now().strftime('%H:%M:%S UAE'),reason=err or 'No fresh expiry price'); active.pop(uid,None); return
 result='DRAW' if price==signal.entry else ('WIN' if ((signal.direction=='UP' and price>signal.entry) or (signal.direction=='DOWN' and price<signal.entry)) else 'LOSS')
 record({'ts':time.time(),'asset':signal.asset,'direction':signal.direction,'confidence':signal.confidence,'expiry':signal.expiry,'entry':signal.entry,'exit':price,'result':result}); reset_daily(); active.pop(uid,None)
 await send_image(uid,'result',signal.asset,direction=result,expiry=signal.expiry,entry=f'{signal.entry:.6f}',exit=f'{price:.6f}',verification='candle-closed',result_time=now().strftime('%H:%M:%S UAE'),reason=f'Entry {signal.entry:.6f} → Exit {price:.6f}')
 if result=='LOSS':
  recovery_until[uid]=time.time()+300; msg=await send_image(uid,'recovery',signal.asset,recovery='15 MIN',quick_recovery='5 MIN',remaining=300,total_seconds=300,next_signal='Automatic after recovery',daily_limit=f'{daily["losses"]}/{MAX_DAILY_LOSSES}');
  if msg: timer_tasks[f'recovery:{uid}']=asyncio.create_task(recovery_timer(uid,msg,signal.asset))
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
  if recovery_until.get(uid,0)>time.time(): log.info('CANDICE RECOVERY WAIT pair=%s remaining=%ss',asset,int(recovery_until[uid]-time.time())); continue
  recovery_until.pop(uid,None); a=await analyze(asset,broker,summary(asset)); log.info('AI ANALYSIS pair=%s decision=%s direction=%s confidence=%s expiry=%s reason=%s',asset,a.decision,a.direction,a.confidence,a.expiry,a.reason)
  if a.decision!='APPROVE':continue
  key=f'{uid}:{asset}:{int(time.time()//300)}'
  if key in sent_keys:continue
  df,e=await broker.candles(asset,60,20,120)
  if e or df is None:continue
  entry=float(df.close.iloc[-1]); entry_ts=time.time(); signal=Signal(asset,a.direction,a.confidence,a.expiry,entry,entry_ts,a.reason); active[uid]=signal; sent_keys.add(key); end=entry_ts+a.expiry*60
  msg=await send_image(uid,'signal',asset,a.direction,a.confidence,a.expiry,a.reason,entry=f'{entry:.6f}',technical='PASSED',signal_time=datetime.fromtimestamp(entry_ts,UAE).strftime('%H:%M:%S UAE'),total_seconds=a.expiry*60,remaining=a.expiry*60,expiry_time=datetime.fromtimestamp(end,UAE).strftime('%H:%M:%S UAE')); asyncio.create_task(result_monitor(uid,signal,msg))
async def scheduler():
 while True:
  wait=INTERVAL-(time.time()%INTERVAL); await asyncio.sleep(max(1,wait))
  try: await scan_once()
  except Exception: log.exception('scan cycle failed')
async def bot_main():
 global tg_app; tg_app=Application.builder().token(TOKEN).build()
 for command,fn in [('start',cmd_start),('access',cmd_access),('update',cmd_update),('assets',cmd_assets),('status',cmd_status),('session',cmd_session)]:tg_app.add_handler(CommandHandler(command,fn))
 tg_app.add_handler(CallbackQueryHandler(asset_callback,r'^asset:')); await tg_app.initialize(); await tg_app.bot.delete_webhook(drop_pending_updates=True); await tg_app.start(); await tg_app.updater.start_polling(drop_pending_updates=True); log.warning('CANDICE TELEGRAM: ONLINE'); asyncio.create_task(broker.connect_forever()); asyncio.create_task(scheduler())
 while True:await asyncio.sleep(3600)
@flask_app.get('/')
def home():return f'{VERSION} ONLINE — FLEX market-data / manual signals only'
@flask_app.get('/health')
def health():return 'OK'
@flask_app.get('/status')
def web_status():return {'version':VERSION,'broker_connected':broker.connected(),'selected_users':len(selected),'auto_trade':False,'martingale':False,'forex_mode':False,'flex_mode':True}
def http():flask_app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')),threaded=True,use_reloader=False)
if __name__=='__main__':
 if not TOKEN:raise RuntimeError('TELEGRAM_BOT_TOKEN is missing')
 threading.Thread(target=http,daemon=True).start();asyncio.run(bot_main())
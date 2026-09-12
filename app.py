from __future__ import annotations
import asyncio, logging, os, threading, time, io
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler
from candice_broker import Broker
from candice_engine import analyze, session_state, technical_snapshot
from candice_memory import record, summary, today_risk
from candice_ui import png_bytes
logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),format='%(asctime)s %(levelname)s %(name)s: %(message)s'); log=logging.getLogger('candice')
VERSION='8.0-FRESH-CANDICE'; UAE=ZoneInfo('Asia/Dubai'); TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','').strip(); ACCESS=os.getenv('ACCESS_CODE','').strip(); OT_TOKEN=os.getenv('OLYMPTRADE_ACCESS_TOKEN','').strip()
INTERVAL=max(60,int(os.getenv('SCAN_INTERVAL_SECONDS','300'))); MAX_DAILY_LOSSES=max(1,int(os.getenv('DAILY_MAX_LOSSES','5'))); MAX_STREAK=max(1,int(os.getenv('MAX_CONSECUTIVE_LOSSES','3'))); AUTO_TRADE=False; MARTINGALE=False; FOREX_MODE=False
flask_app=Flask(__name__); broker=Broker(OT_TOKEN); tg_app=None; users=set(); selected={}; active={}; sent_keys=set(); daily={'date':'','losses':0,'streak':0}
def now(): return datetime.now(UAE)
def reset_daily():
    d=now().date().isoformat(); daily['date']=d; r=today_risk(UAE); daily['losses']=r['losses']; daily['streak']=r['streak']
def common(title,body,icon='🤖'):
    return f'''{icon} CANDICE AI • LIVE FLEX\n\n{title}\n\n{body}\n\n━━━━━━━━━━━━━━━━\n📡 LIVE MARKET • AI ANALYSIS\n💠 FLEX / FIXED-TIME • MANUAL ONLY\n🚫 AUTO-TRADE OFF • 🚫 MARTINGALE OFF'''

def session_text():
    s=session_state(); state='🟢 SIGNAL SESSION ACTIVE' if s=='SIGNAL' else '🧠 RESEARCH ONLY'
    return common(state,f'''📡 Market research: 24/7\n⏱ Scan cycle: every 5 minutes\n🎯 Signal: selected FLEX asset only\n⏱ AI duration: 2 / 3 / 5 / 10 / 15 MIN\n\n🕐 UAE: {now().strftime('%H:%M:%S')}''','🧠')

async def send_image(cid,kind='selected',asset='ASIA_X',direction='',confidence=0,expiry=0,reason='',**kwargs):
    if tg_app is None:
        log.warning('TELEGRAM IMAGE SKIPPED: bot not initialized kind=%s asset=%s',kind,asset); return
    try:
        payload=png_bytes(kind=kind,asset=asset,direction=direction,confidence=confidence,expiry=expiry,reason=reason,**kwargs)
        if not isinstance(payload,io.BytesIO): payload=io.BytesIO(payload)
        payload.seek(0); payload.name='candice-update.png'
        log.info('TELEGRAM IMAGE SEND START kind=%s asset=%s bytes=%s',kind,asset,payload.getbuffer().nbytes)
        await tg_app.bot.send_photo(chat_id=cid,photo=InputFile(payload,filename='candice-update.png'),caption='Candice AI • Live Market • Manual Trade Only',read_timeout=30,write_timeout=30,connect_timeout=15,pool_timeout=15)
        log.info('TELEGRAM IMAGE SENT kind=%s asset=%s',kind,asset)
    except Exception as e: log.exception('TELEGRAM IMAGE FAILED kind=%s asset=%s: %s',kind,asset,e)

async def send_update_text(cid):
    if tg_app is None:return
    await tg_app.bot.send_message(chat_id=cid,text='''🚀 CANDICE AI v8\n\nUnified visual update model is active.\nOperational events use the same CANDICE branded image model.\n\nASSET SELECTED • AI SIGNAL • SESSION TIME • EXPIRY REACHED • TRADE RESULT • RECOVERY TIME\n\nFLEX / FIXED-TIME • MANUAL ONLY\n🚫 Auto-trade OFF • 🚫 Martingale OFF''')

def keyboard(assets):
    rows=[]
    for i in range(0,len(assets),2):rows.append([InlineKeyboardButton(a,callback_data=f'asset:{a}') for a in assets[i:i+2]])
    return InlineKeyboardMarkup(rows or [[InlineKeyboardButton('No live FLEX assets',callback_data='noop')]])

async def cmd_access(update,ctx):
    if not ACCESS or not ctx.args or ctx.args[0].strip()!=ACCESS:await update.message.reply_text('🔒 CANDICE AI\n\nAccess denied.');return
    users.add(update.effective_user.id); await send_update_text(update.effective_user.id); await cmd_assets(update,ctx)
async def cmd_start(update,ctx):
    if update.effective_user.id not in users:await update.message.reply_text('🔒 CANDICE AI\n\nUse /access <code> first.');return
    await cmd_assets(update,ctx)
async def cmd_update(update,ctx):
    if update.effective_user.id not in users:await update.message.reply_text('🔒 CANDICE AI\n\nUse /access <code> first.');return
    await send_update_text(update.effective_user.id)
async def cmd_assets(update,ctx):
    assets=await broker.live_assets(); body='''Select ONE live FLEX asset.\n\n🟢 Fresh 1-minute candle verification is required.\n📡 Candice AI research runs continuously.\n🚫 Forex mode: OFF'''
    if assets:body+='\n\n'+('\n'.join('• '+a for a in assets))
    else:body+='\n\n⚠️ No live FLEX assets currently confirmed.'
    text=common('🟢 FLEX ASSET SELECTOR',body,'🎯')
    if update.message:await update.message.reply_text(text,reply_markup=keyboard(assets))
    else:await update.callback_query.message.reply_text(text,reply_markup=keyboard(assets))
async def cmd_status(update,ctx):
    reset_daily(); await send_image(update.effective_user.id,'status',asset=selected.get(update.effective_user.id,'SYSTEM'),status_text=f'Broker: {"CONNECTED" if broker.connected() else "DISCONNECTED"} • Session: {session_state()}')
async def cmd_session(update,ctx):
    s=session_state(); await send_image(update.effective_user.id,'session',asset=selected.get(update.effective_user.id,'SYSTEM'),active=s=='SIGNAL',session_start='Current block',session_end='Current block',next_session='Next 3h block',countdown='Live')

async def asset_callback(update,ctx):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    if uid not in users or not q.data.startswith('asset:'):return
    asset=q.data.split(':',1)[1].upper(); live=await broker.live_assets()
    if asset not in live:await q.edit_message_text('⚠️ Asset is no longer confirmed live. Use /assets again.');return
    selected[uid]=asset; await q.edit_message_text(common(f'✅ ASSET SELECTED — {asset}','''🟢 Fresh 1-minute candle verified\n🤖 Candice AI analysis: ON\n📡 Research: 24/7\n🚀 Signals: SIGNAL SESSION only\n⏱ Next scan window: next 5 minutes\n⏱ AI duration: 2 / 3 / 5 / 10 / 15 MIN''','🎯')); await send_image(uid,'selected',asset)

@dataclass
class Signal:asset:str;direction:str;confidence:int;expiry:int;entry:float;entry_ts:float;reason:str

async def result_monitor(uid,signal):
    expiry=signal.entry_ts+signal.expiry*60
    while time.time()<expiry:await asyncio.sleep(min(10,max(1,expiry-time.time())))
    await send_image(uid,'expiry',signal.asset,direction=signal.direction,expiry=signal.expiry,entry=f'{signal.entry:.6f}',expiry_time=datetime.fromtimestamp(expiry,UAE).strftime('%H:%M:%S UAE'))
    price=None;err=''
    for _ in range(5):
        df,e=await broker.candles(signal.asset,60,20,120);err=e or ''
        if df is not None and not df.empty:price=float(df.close.iloc[-1]);break
        await asyncio.sleep(3)
    if price is None:
        await send_image(uid,'result',signal.asset,direction='UNRESOLVED',expiry=signal.expiry,entry=f'{signal.entry:.6f}',exit='—',verification='No fresh expiry candle',result_time=now().strftime('%H:%M:%S UAE'),reason=err or 'No fresh expiry price');active.pop(uid,None);return
    result='DRAW' if price==signal.entry else ('WIN' if ((signal.direction=='UP' and price>signal.entry) or (signal.direction=='DOWN' and price<signal.entry)) else 'LOSS')
    record({'ts':time.time(),'asset':signal.asset,'direction':signal.direction,'confidence':signal.confidence,'expiry':signal.expiry,'entry':signal.entry,'exit':price,'result':result});reset_daily();active.pop(uid,None)
    await send_image(uid,'result',signal.asset,direction=result,expiry=signal.expiry,entry=f'{signal.entry:.6f}',exit=f'{price:.6f}',verification='candle-closed',result_time=now().strftime('%H:%M:%S UAE'),reason=f'Entry {signal.entry:.6f} → Exit {price:.6f}')
    if result=='LOSS':await send_image(uid,'recovery',signal.asset,recovery='15 MIN',quick_recovery='5 MIN',next_signal='Automatic',daily_limit=f'{daily["losses"]}/{MAX_DAILY_LOSSES}',streak_stop=f'{daily["streak"]}/{MAX_STREAK}')

async def scan_once():
    reset_daily();assets=await broker.live_assets();log.info('CANDICE SCAN START assets=%d session=%s users=%d',len(assets),session_state(),len(users))
    sem=asyncio.Semaphore(6)
    async def research(asset):
        async with sem:
            df,e=await broker.candles(asset,60,60,360)
            if e is None and df is not None:
                s=technical_snapshot(df);record({'ts':time.time(),'asset':asset,'research':True,'direction':s['direction'],'strength':s['strength'],'rsi':s['rsi14'],'adx':s['adx14']});log.info('CANDICE RESEARCH pair=%s direction=%s strength=%.2f RSI=%.1f ADX=%.1f',asset,s['direction'],s['strength'],s['rsi14'],s['adx14'])
            else:log.info('CANDICE RESEARCH REJECT pair=%s reason=%s',asset,e or 'no fresh candles')
    await asyncio.gather(*(research(a) for a in assets[:30]))
    if session_state()!='SIGNAL' or not users or daily['losses']>=MAX_DAILY_LOSSES or daily['streak']>=MAX_STREAK:return
    for uid,asset in list(selected.items()):
        if uid in active or asset not in assets:continue
        a=await analyze(asset,broker,summary(asset));log.info('AI ANALYSIS pair=%s decision=%s direction=%s confidence=%s expiry=%s reason=%s',asset,a.decision,a.direction,a.confidence,a.expiry,a.reason)
        if a.decision!='APPROVE':continue
        key=f'{uid}:{asset}:{int(time.time()//300)}'
        if key in sent_keys:continue
        df,e=await broker.candles(asset,60,20,120)
        if e or df is None:continue
        entry=float(df.close.iloc[-1]);signal=Signal(asset,a.direction,a.confidence,a.expiry,entry,time.time(),a.reason);active[uid]=signal;sent_keys.add(key)
        await send_image(uid,'signal',asset,a.direction,a.confidence,a.expiry,a.reason,entry=f'{entry:.6f}',technical='PASSED',trend=a.direction,expiry_time=(now().timestamp()+a.expiry*60 and datetime.fromtimestamp(time.time()+a.expiry*60,UAE).strftime('%H:%M:%S UAE')));await tg_app.bot.send_message(chat_id=uid,text=common(f'🔥 CANDICE AI SIGNAL — {asset}',f'''Direction: {a.direction}\n💰 Entry: {entry:.6f}\n⏱ Duration: {a.expiry} MIN\n🤖 Candice AI: APPROVED ({a.confidence}%)\n🧠 Reason: {a.reason}\n\n⏳ Active signal — result will be verified after the duration.''','🚨'));asyncio.create_task(result_monitor(uid,signal))

async def scheduler():
    while True:
        wait=INTERVAL-(time.time()%INTERVAL);await asyncio.sleep(max(1,wait))
        try:await scan_once()
        except Exception:log.exception('scan cycle failed')
async def bot_main():
    global tg_app
    tg_app=Application.builder().token(TOKEN).build()
    for command,fn in [('start',cmd_start),('access',cmd_access),('update',cmd_update),('assets',cmd_assets),('status',cmd_status),('session',cmd_session)]:tg_app.add_handler(CommandHandler(command,fn))
    tg_app.add_handler(CallbackQueryHandler(asset_callback,r'^asset:'))
    await tg_app.initialize();await tg_app.bot.delete_webhook(drop_pending_updates=True);await tg_app.start();await tg_app.updater.start_polling(drop_pending_updates=True);log.warning('CANDICE TELEGRAM: ONLINE')
    asyncio.create_task(broker.connect_forever());asyncio.create_task(scheduler())
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
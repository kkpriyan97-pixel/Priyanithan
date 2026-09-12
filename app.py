from __future__ import annotations
import asyncio, logging, os, threading, time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from candice_broker import Broker, FLEX_ASSETS
from candice_engine import analyze, session_state, technical_snapshot
from candice_memory import record, summary
from candice_ui import gif_bytes

logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log=logging.getLogger('candice')
VERSION='8.0-FRESH-CANDICE'; UAE=ZoneInfo('Asia/Dubai')
TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','').strip(); ACCESS=os.getenv('ACCESS_CODE','').strip(); OT_TOKEN=os.getenv('OLYMPTRADE_ACCESS_TOKEN','').strip(); CHAT=os.getenv('TELEGRAM_CHAT_ID','').strip()
INTERVAL=max(60,int(os.getenv('SCAN_INTERVAL_SECONDS','300'))); MAX_DAILY_LOSSES=max(1,int(os.getenv('DAILY_MAX_LOSSES','5'))); MAX_STREAK=max(1,int(os.getenv('MAX_CONSECUTIVE_LOSSES','3')))
AUTO_TRADE=False; MARTINGALE=False; FOREX_MODE=False
flask_app=Flask(__name__); broker=Broker(OT_TOKEN); tg_app=None; users=set(); selected={}; active={}; sent_keys=set(); daily={'date':'','losses':0,'streak':0}

def now(): return datetime.now(UAE)
def reset_daily():
    d=now().date().isoformat()
    if daily['date']!=d: daily.update(date=d,losses=0,streak=0)
def session_text():
    s=session_state(); return f'''🤖 CANDICE AI v8\n\n{'🟢 SIGNAL SESSION' if s=='SIGNAL' else '🧠 RESEARCH ONLY'}\n\n📡 Market research: 24/7\n⏱ Scan boundary: every 5 minutes\n🎯 Signal generation: selected FLEX asset only\n💠 Mode: FLEX / FIXED-TIME\n🚫 Forex mode: OFF\n🚫 Auto-trade: OFF\n🚫 Martingale: OFF\n\nUAE: {now().strftime('%H:%M:%S')}'''
async def send_card(cid,kind='selected',asset='ASIA_X',direction='',confidence=0,expiry=0,reason=''):
    if tg_app is None:return
    try: await tg_app.bot.send_animation(chat_id=cid,animation=gif_bytes(kind=kind,asset=asset,direction=direction,confidence=confidence,expiry=expiry,reason=reason),caption='Candice AI • Live Market • Manual Trade Only')
    except Exception as e: log.warning('visual card failed: %s',e)
def keyboard(assets):
    rows=[]
    for i in range(0,len(assets),2): rows.append([InlineKeyboardButton(a,callback_data=f'asset:{a}') for a in assets[i:i+2]])
    return InlineKeyboardMarkup(rows or [[InlineKeyboardButton('No live FLEX assets',callback_data='noop')]])
async def cmd_access(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not ACCESS or not ctx.args or ctx.args[0].strip()!=ACCESS: await update.message.reply_text('🔒 Access denied.'); return
    users.add(update.effective_user.id); await cmd_assets(update,ctx)
async def cmd_start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in users: await update.message.reply_text('🔒 Use /access <code> first.'); return
    await cmd_assets(update,ctx)
async def cmd_assets(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    assets=await broker.live_assets()
    if not assets: assets=sorted(set(broker.catalog)&set(FLEX_ASSETS))
    text='🟢 CANDICE FLEX ASSET SELECTOR\n\nSelect ONE live FLEX asset.\nForex mode is OFF.\nAuto-trade is OFF.\n\n'+('\n'.join('• '+a for a in assets) if assets else 'No live assets currently confirmed.')
    if update.message: await update.message.reply_text(text,reply_markup=keyboard(assets))
    else: await update.callback_query.message.reply_text(text,reply_markup=keyboard(assets))
async def cmd_status(update,ctx):
    reset_daily(); a=selected.get(update.effective_user.id,'None'); await update.message.reply_text(f'''🧠 CANDICE STATUS\nVersion: {VERSION}\nBroker: {'CONNECTED' if broker.connected() else 'DISCONNECTED'}\nSelected: {a}\nSession: {session_state()}\nFLEX: ON\nForex: OFF\nAuto-trade: OFF\nMartingale: OFF\nDaily losses: {daily['losses']}/{MAX_DAILY_LOSSES}\nConsecutive losses: {daily['streak']}/{MAX_STREAK}\nScan: every 5 minutes''')
async def cmd_session(update,ctx): await update.message.reply_text(session_text())
async def asset_callback(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    if uid not in users or not q.data.startswith('asset:'):return
    asset=q.data.split(':',1)[1].upper(); live=await broker.live_assets()
    if asset not in live: await q.edit_message_text('⚠️ Asset is no longer confirmed live. Use /assets again.'); return
    selected[uid]=asset; await q.edit_message_text(f'✅ {asset} selected.\n\nCandice researches continuously and generates signals only during SIGNAL SESSION.\n\nFLEX / FIXED-TIME • MANUAL ONLY'); await send_card(uid,'selected',asset)
@dataclass
class Signal:
    asset:str; direction:str; confidence:int; expiry:int; entry:float; entry_ts:float; reason:str
async def result_monitor(uid,signal):
    expiry=signal.entry_ts+signal.expiry*60
    while time.time()<expiry: await asyncio.sleep(min(10,max(1,expiry-time.time())))
    price=None; err=''
    for _ in range(5):
        df,e=await broker.candles(signal.asset,60,20,120); err=e or ''
        if df is not None and not df.empty: price=float(df.close.iloc[-1]); break
        await asyncio.sleep(3)
    if price is None: await send_card(uid,'result',signal.asset,direction='UNRESOLVED',reason=err or 'No fresh expiry price'); active.pop(uid,None); return
    result='DRAW' if price==signal.entry else ('WIN' if ((signal.direction=='UP' and price>signal.entry) or (signal.direction=='DOWN' and price<signal.entry)) else 'LOSS')
    reset_daily()
    if result=='LOSS': daily['losses']+=1; daily['streak']+=1
    elif result=='WIN': daily['streak']=0
    record({'ts':time.time(),'asset':signal.asset,'direction':signal.direction,'confidence':signal.confidence,'expiry':signal.expiry,'entry':signal.entry,'exit':price,'result':result})
    active.pop(uid,None); await send_card(uid,'result',signal.asset,direction=result,reason=f'Entry {signal.entry:.6f} → Exit {price:.6f}')
    await tg_app.bot.send_message(chat_id=uid,text=f'📊 RESULT {result}\n{signal.asset} • {signal.direction} • {signal.expiry}m\nEntry: {signal.entry:.6f}\nExpiry: {price:.6f}\n\nManual signal outcome — not broker account P/L.')
async def scan_once():
    reset_daily(); assets=await broker.live_assets()
    for asset in assets[:30]:
        df,e=await broker.candles(asset,60,60,360)
        if e is None and df is not None:
            s=technical_snapshot(df); record({'ts':time.time(),'asset':asset,'research':True,'direction':s['direction'],'strength':s['strength'],'rsi':s['rsi14'],'adx':s['adx14']})
    if session_state()!='SIGNAL' or not users or daily['losses']>=MAX_DAILY_LOSSES or daily['streak']>=MAX_STREAK:return
    for uid,asset in list(selected.items()):
        if uid in active or asset not in assets:continue
        a=await analyze(asset,broker,summary(asset))
        if a.decision!='APPROVE':continue
        boundary=int(time.time()//300); key=f'{uid}:{asset}:{boundary}'
        if key in sent_keys:continue
        df,e=await broker.candles(asset,60,20,120)
        if e or df is None:continue
        entry=float(df.close.iloc[-1]); signal=Signal(asset,a.direction,a.confidence,a.expiry,entry,time.time(),a.reason); active[uid]=signal; sent_keys.add(key)
        await send_card(uid,'signal',asset,a.direction,a.confidence,a.expiry,a.reason); await tg_app.bot.send_message(chat_id=uid,text=f'🚨 CANDICE AI SIGNAL\n\nAsset: {asset}\nDirection: {a.direction}\nConfidence: {a.confidence}%\nExpiry: {a.expiry} minutes\nEntry: {entry:.6f}\n\n🧠 {a.reason}\n\nMANUAL TRADE ONLY • AUTO-TRADE OFF'); asyncio.create_task(result_monitor(uid,signal))
async def scheduler():
    while True:
        try: await scan_once()
        except Exception: log.exception('scan cycle failed')
        await asyncio.sleep(max(30,INTERVAL))
async def bot_main():
    global tg_app
    tg_app=Application.builder().token(TOKEN).build(); tg_app.add_handler(CommandHandler('start',cmd_start)); tg_app.add_handler(CommandHandler('access',cmd_access)); tg_app.add_handler(CommandHandler('assets',cmd_assets)); tg_app.add_handler(CommandHandler('status',cmd_status)); tg_app.add_handler(CommandHandler('session',cmd_session)); tg_app.add_handler(CallbackQueryHandler(asset_callback,r'^asset:'))
    await tg_app.initialize(); await tg_app.bot.delete_webhook(drop_pending_updates=True); await tg_app.start(); await tg_app.updater.start_polling(drop_pending_updates=True); log.warning('CANDICE TELEGRAM: ONLINE')
    asyncio.create_task(broker.connect_forever()); asyncio.create_task(scheduler())
    while True: await asyncio.sleep(3600)
@flask_app.get('/')
def home(): return f'{VERSION} ONLINE — FLEX market-data / manual signals only'
@flask_app.get('/health')
def health(): return 'OK'
@flask_app.get('/status')
def web_status(): return {'version':VERSION,'broker_connected':broker.connected(),'selected_users':len(selected),'auto_trade':False,'martingale':False,'forex_mode':False,'flex_mode':True}
def http(): flask_app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')),threaded=True,use_reloader=False)
if __name__=='__main__':
    if not TOKEN: raise RuntimeError('TELEGRAM_BOT_TOKEN is missing')
    threading.Thread(target=http,daemon=True).start(); asyncio.run(bot_main())

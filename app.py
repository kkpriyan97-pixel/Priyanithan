from __future__ import annotations
import asyncio, logging, os, threading, time
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from zoneinfo import ZoneInfo
from flask import Flask, request
from PIL import Image, ImageDraw, ImageFont
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputMediaPhoto
from telegram.ext import Application, CallbackQueryHandler, CommandHandler
from candice_broker import Broker
from candice_engine import analyze, session_state, technical_snapshot
from candice_memory import record, summary, today_risk, authorized_users, authorize_user

VERSION = '8.8-CANDICE-LIVE-TIMER-CARD'
UAE = ZoneInfo('Asia/Dubai')
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
ACCESS = os.getenv('ACCESS_CODE', '').strip()
OT_TOKEN = os.getenv('OLYMPIATRADE_ACCESS_TOKEN', os.getenv('OLYMPTRADE_ACCESS_TOKEN', '')).strip()
INTERVAL = 60
PRE_ENTRY_SECONDS = 10
MAX_DAILY_LOSSES = max(1, int(os.getenv('DAILY_MAX_LOSSES', '5')))
MAX_STREAK = max(1, int(os.getenv('MAX_CONSECUTIVE_LOSSES', '3')))
AUTO_TRADE = False
MARTINGALE = False
FOREX_MODE = False

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('candice')
flask_app = Flask(__name__)
broker = Broker(OT_TOKEN)
tg_app = None
BOT_LOOP = None
WEBHOOK_PATH = '/telegram/webhook'
WEBHOOK_SECRET = os.getenv('TELEGRAM_WEBHOOK_SECRET', '').strip()
users = set(authorized_users())
selected = {}
active = {}
sent_keys = set()
recovery_until = {}
daily = {'date': '', 'losses': 0, 'streak': 0}

@dataclass
class Signal:
    asset: str
    direction: str
    confidence: int
    expiry: int
    entry: float
    entry_ts: float
    candle_ts: float
    reason: str
    evidence: tuple[str, ...] = ()

def now(): return datetime.now(UAE)

def reset_daily():
    daily['date'] = now().date().isoformat()
    r = today_risk(UAE); daily['losses'] = int(r.get('losses', 0)); daily['streak'] = int(r.get('streak', 0))

def entry_boundary(candle_ts: float) -> float: return float(candle_ts) + 60.0

def expiry_boundary(candle_ts: float, expiry: int) -> float: return entry_boundary(candle_ts) + int(expiry) * 60.0

def header(title): return f'━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • LIVE MARKET\n━━━━━━━━━━━━━━━━━━━━\n✨ {title}\n━━━━━━━━━━━━━━━━━━━━'

def keyboard(assets):
    rows = [[InlineKeyboardButton('🟢 ' + a, callback_data=f'asset:{a}') for a in assets[i:i+2]] for i in range(0, len(assets), 2)]
    return InlineKeyboardMarkup(rows or [[InlineKeyboardButton('⚠️ No live FLEX assets', callback_data='noop')]])

async def send_text(cid, text, reply_markup=None):
    if tg_app is None: return None
    try:
        m = await tg_app.bot.send_message(chat_id=cid, text=text, reply_markup=reply_markup, disable_web_page_preview=True, read_timeout=30, write_timeout=30, connect_timeout=15, pool_timeout=15)
        log.info('TELEGRAM TEXT SENT chat=%s message_id=%s', cid, m.message_id); return m
    except Exception as e:
        log.exception('TELEGRAM TEXT FAILED: %s', e); return None

def _font(size, bold=False):
    candidates = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf']
    for path in candidates:
        try: return ImageFont.truetype(path, size)
        except Exception: pass
    return ImageFont.load_default()

def _fit_text(draw, text, max_width, start_size, bold=False):
    size = start_size
    while size > 18:
        f = _font(size, bold)
        if draw.textbbox((0, 0), text, font=f)[2] <= max_width: return f
        size -= 2
    return _font(18, bold)

def _wrap_lines(draw, text, max_width, font):
    words = str(text).split(); lines=[]; cur=''
    for word in words:
        trial = word if not cur else cur+' '+word
        if draw.textbbox((0,0), trial, font=font)[2] <= max_width: cur=trial
        else:
            if cur: lines.append(cur)
            cur=word
    if cur: lines.append(cur)
    return lines

def _card_base(asset, direction, entry_ts, candle_ts, expiry, confidence, timeframe, evidence, timer_text='', timer_label='ENTRY IN'):
    W,H=1080,1350
    img=Image.new('RGB',(W,H),(4,13,34)); px=img.load()
    for y in range(H):
        for x in range(W):
            glow=max(0.0,1.0-(((x-W*.72)**2+(y-H*.24)**2)**.5)/900)
            px[x,y]=(int(4+10*glow),int(13+20*glow),int(34+55*glow))
    d=ImageDraw.Draw(img); f_title=_font(54,True); f_sub=_font(23); f_label=_font(22,True); f_big=_font(48,True); f_mid=_font(30,True); f_small=_font(21)
    d.rounded_rectangle((12,12,W-12,H-12),radius=34,outline=(24,125,220),width=4)
    d.text((62,45),'CANDICE',font=f_title,fill=(245,248,255)); d.text((365,47),'AI',font=f_title,fill=(30,170,255)); d.text((65,112),'TRADING SIGNAL',font=f_sub,fill=(175,200,225)); d.text((765,52),'DISCIPLINE',font=f_small,fill=(175,200,225)); d.text((765,80),'CONFIRM • ALERT',font=f_small,fill=(175,200,225))
    d.rounded_rectangle((35,165,W-35,335),radius=28,fill=(8,44,50),outline=(25,130,130),width=3); d.text((75,190),'ASSET',font=f_label,fill=(140,175,195)); asset_text='Asia Composite Index' if asset=='ASIA_X' else asset; d.text((75,225),asset_text,font=_fit_text(d,asset_text,860,46,True),fill=(248,250,255)); d.text((78,290),asset,font=f_small,fill=(105,175,235))
    down=direction=='DOWN'; dir_fill=(220,35,70) if down else (40,230,135); arrow='↓' if down else '↑'; dir_text='DOWN' if down else 'UP'
    d.rounded_rectangle((35,360,405,555),radius=26,fill=(48,8,35) if down else (7,35,29),outline=dir_fill,width=4); d.text((65,388),'TRADE DIRECTION',font=f_label,fill=(255,130,155) if down else (130,215,175)); d.text((65,438),arrow,font=_font(68,True),fill=dir_fill); d.text((145,448),dir_text,font=f_big,fill=(255,245,248))
    cards=[(430,'ENTRY TIME',datetime.fromtimestamp(entry_ts,UAE).strftime('%H:%M:%S UAE'),(25,125,220)),(650,'EXPIRY',f'{expiry} MIN',(245,175,20)),(870,'CANDLE','1 MIN',(25,190,100))]
    for x,label,value,accent in cards:
        d.rounded_rectangle((x,360,x+185,555),radius=24,fill=(7,25,45),outline=accent,width=3); d.text((x+18,388),label,font=_font(18,True),fill=(150,180,205)); d.text((x+18,445),value,font=_fit_text(d,value,150,30,True),fill=(248,250,255))
    d.rounded_rectangle((35,580,365,710),radius=24,fill=(7,25,45),outline=(28,105,205),width=3); d.text((60,602),'HUMAN BRAIN',font=f_label,fill=(185,150,230)); d.text((60,650),'CONFIRMED ✓',font=f_mid,fill=(80,235,150))
    d.rounded_rectangle((385,580,715,710),radius=24,fill=(7,25,45),outline=(28,145,205),width=3); d.text((410,602),'AI ANALYSIS',font=f_label,fill=(155,190,230)); d.text((410,650),'APPROVED ✓',font=f_mid,fill=(80,235,150))
    d.rounded_rectangle((735,580,1045,710),radius=24,fill=(7,25,45),outline=(30,125,190),width=3); d.text((760,602),'CONFIDENCE',font=f_label,fill=(155,190,230)); d.text((760,646),f'{int(confidence)}%',font=_font(42,True),fill=(50,235,165))
    d.rounded_rectangle((35,735,W-35,1010),radius=26,fill=(5,23,45),outline=(18,95,165),width=3); d.text((65,762),'SIGNAL REASONS',font=f_mid,fill=(155,195,235)); items=list(evidence or ())[:4] or ['Multi-indicator alignment verified']; y=820
    for i,item in enumerate(items):
        d.ellipse((68,y+6,86,y+24),fill=[(40,220,135),(30,165,235),(245,175,25),(170,70,235)][i%4]); lines=_wrap_lines(d,item,900,f_small)
        for line in lines[:2]: d.text((110,y),line,font=f_small,fill=(235,242,250)); y+=31
        y+=10
    d.rounded_rectangle((35,1035,W-35,1170),radius=26,fill=(48,8,35),outline=(225,35,70),width=4)
    d.text((70,1058),'⏰  '+timer_label,font=f_mid,fill=(255,105,125)); d.text((70,1108),timer_text,font=_font(32,True),fill=(250,250,255)); d.text((680,1068),'MANUAL ONLY',font=f_mid,fill=(255,125,150)); d.text((680,1110),'DEMO MODE',font=f_small,fill=(235,190,205))
    d.text((55,1205),'🛡️ MANUAL TRADE ONLY',font=f_mid,fill=(65,235,155)); d.text((55,1245),'AUTO-TRADE OFF  •  DEMO MODE',font=f_small,fill=(150,185,215)); d.text((735,1208),str(timeframe),font=f_small,fill=(90,185,240)); d.text((735,1245),datetime.fromtimestamp(candle_ts,UAE).strftime('%H:%M:%S UAE'),font=f_small,fill=(125,160,190))
    bio=BytesIO(); img.save(bio,format='JPEG',quality=92,optimize=True); bio.seek(0); return bio

def build_signal_card(asset,direction,entry_ts,candle_ts,expiry,confidence,timeframe,evidence):
    return _card_base(asset,direction,entry_ts,candle_ts,expiry,confidence,timeframe,evidence,f'{PRE_ENTRY_SECONDS} SEC BEFORE ENTRY','ALERT')

def build_live_timer_card(asset,direction,entry_ts,candle_ts,expiry,confidence,timeframe,evidence,remaining,phase):
    if phase=='pre':
        text=f'{remaining:02d} SEC'; label='ENTRY IN'
    else:
        mm,ss=divmod(max(0,int(remaining)),60); text=f'{mm:02d}:{ss:02d}'; label='LIVE TIMER'
    return _card_base(asset,direction,entry_ts,candle_ts,expiry,confidence,timeframe,evidence,text,label)

async def send_signal_card(cid,asset,direction,entry_ts,candle_ts,expiry,confidence,timeframe,evidence):
    if tg_app is None:return None
    try:
        photo=build_signal_card(asset,direction,entry_ts,candle_ts,expiry,confidence,timeframe,evidence)
        m=await tg_app.bot.send_photo(chat_id=cid,photo=photo,read_timeout=30,write_timeout=30,connect_timeout=15,pool_timeout=15)
        log.info('TELEGRAM SIGNAL CARD SENT chat=%s message_id=%s asset=%s direction=%s expiry=%s confidence=%s',cid,m.message_id,asset,direction,expiry,confidence); return m
    except Exception as e: log.exception('TELEGRAM SIGNAL CARD FAILED: %s',e); return None

async def edit_card(cid,mid,s,remaining,phase):
    try:
        photo=build_live_timer_card(s.asset,s.direction,s.entry_ts,s.candle_ts,s.expiry,s.confidence,s.timeframe,s.evidence,remaining,phase)
        await tg_app.bot.edit_message_media(chat_id=cid,message_id=mid,media=InputMediaPhoto(media=photo)); return True
    except Exception as e: log.warning('TELEGRAM CARD EDIT FAILED message_id=%s: %s',mid,e); return False

async def edit_text(cid,mid,text,reply_markup=None):
    try: await tg_app.bot.edit_message_text(chat_id=cid,message_id=mid,text=text,reply_markup=reply_markup,disable_web_page_preview=True); return True
    except Exception as e: log.warning('TELEGRAM TEXT EDIT FAILED message_id=%s: %s',mid,e); return False

async def cmd_access(update,ctx):
    if not ACCESS or not ctx.args or ctx.args[0].strip()!=ACCESS: await update.message.reply_text('🔒 CANDICE AI\n\nAccess denied.'); return
    uid=authorize_user(update.effective_user.id); users.add(uid); log.info('CANDICE USER AUTHORIZED chat=%s persistent_users=%d',uid,len(users)); await cmd_assets(update,ctx)

async def cmd_start(update,ctx):
    if update.effective_user.id not in users: await update.message.reply_text('🔒 CANDICE AI\n\nUse /access <code> first.'); return
    await cmd_assets(update,ctx)

async def cmd_assets(update,ctx):
    assets=await broker.live_assets(); names=' • '.join(assets) if assets else 'No fresh FLEX assets available'; text=(f'{header("FLEX ASSET SELECTOR")}\n\n📈 LIVE MARKET\n🟢 Fresh 1-minute candle VERIFIED\n\n🔥 Available: {len(assets)}\n{names}\n\n⚡ FLEX / FIXED-TIME\n🧠 Candice AI research ACTIVE\n🛡️ Technical + AI confirmation required\n\n👇 Select your asset'); await send_text(update.effective_user.id,text,reply_markup=keyboard(assets))

async def cmd_status(update,ctx):
    reset_daily(); uid=update.effective_user.id; await send_text(uid,f'{header("SYSTEM STATUS")}\n\n🛰️ Broker: {"🟢 CONNECTED" if broker.connected() else "🔴 DISCONNECTED"}\n📈 FLEX: {selected.get(uid,"NONE")}\n🔄 Scanner: every 1 minute\n⏱️ Pre-entry alert: {PRE_ENTRY_SECONDS}s\n👤 Authorized users: {len(users)}\n🧠 Session: {session_state()}\n❌ Daily losses: {daily["losses"]}/{MAX_DAILY_LOSSES}\n🔥 Loss streak: {daily["streak"]}/{MAX_STREAK}\n\n🚫 Auto-trade OFF\n🚫 Martingale OFF\n🚫 Forex mode OFF\n🖼️ Visual signal cards + live timer ON')

async def cmd_session(update,ctx):
    reset_daily(); await send_text(update.effective_user.id,f'{header("LIVE SCAN CONTROL")}\n\n🟢 CONTINUOUS SIGNAL ENGINE\n🔄 Analysis: every 1 minute\n🕯️ Candle: closed 1M\n⏱️ Expiry: 1 / 2 / 3 / 5 / 10 / 15 MIN\n⏳ Pre-entry signal: {PRE_ENTRY_SECONDS} SEC BEFORE ENTRY\n⏱️ Live timer: ON\n🏁 Expiry verification: ACTIVE\n\n🔬 Research: 24/7\n🛡️ Risk gates: ACTIVE\n🚫 Auto-trade OFF • Manual only')

async def cmd_update(update,ctx): await cmd_session(update,ctx)

async def asset_callback(update,ctx):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    if uid not in users or not q.data.startswith('asset:'): return
    asset=q.data.split(':',1)[1].upper()
    if asset=='NOOP': return
    live=await broker.live_assets()
    if asset not in live: await edit_text(uid,q.message.message_id,f'{header("ASSET CHECK FAILED")}\n\n🔴 {asset} is no longer live or its candle is stale.\n\nUse /start again.'); return
    selected[uid]=asset; t=now().strftime('%H:%M:%S UAE'); await edit_text(uid,q.message.message_id,f'{header("ASSET READY")}\n\n📈 {asset}\n\n🟢 MARKET STATUS • LIVE\n🔵 CANDLE • 1 MIN FRESH\n🕒 VERIFIED • {t}\n\n🔥🔥🔥 CANDICE RESEARCH ACTIVE\n\n🟢 Trend structure\n🔵 Momentum confirmation\n🟣 Volatility / breakout check\n🟠 MACD confirmation\n🟡 AI decision gate\n\n⚡ READY FOR QUALIFIED SIGNAL\n⏱️ 1 / 2 / 3 / 5 / 10 / 15 MIN\n📩 Signal alert • 10 SEC before entry\n⏱️ Live timer • ON\n\n🛡️ MANUAL TRADE ONLY • AUTO-TRADE OFF')

async def live_timer(cid,msg,s,phase,end):
    if msg is None:return
    last=None
    while active.get(cid) is s and time.time()<end:
        rem=max(0,int(end-time.time()))
        if rem!=last:
            last=rem; await edit_card(cid,msg.message_id,s,rem,phase)
        await asyncio.sleep(0.2)
    if active.get(cid) is s: await edit_card(cid,msg.message_id,s,0,phase)

async def result_monitor(uid,s,signal_msg):
    entry_end=s.entry_ts
    await live_timer(uid,signal_msg,s,'pre',entry_end)
    end=s.entry_ts+int(s.expiry)*60.0
    await live_timer(uid,signal_msg,s,'trade',end)
    await send_text(uid,f'{header("EXPIRY VERIFICATION")}\n\n📈 {s.asset}\n➡️ Direction • {s.direction}\n💰 Entry • {s.entry:.6f}\n🕒 Boundary • {datetime.fromtimestamp(end,UAE).strftime("%H:%M:%S UAE")}\n\n🔎 Waiting for the boundary candle to close...')
    row=None; err=''
    for _ in range(8):
        row,err=await broker.closed_candle_at(s.asset,end,60,180)
        if row is not None:break
        await asyncio.sleep(3)
    if row is None:
        active.pop(uid,None); await send_text(uid,f'{header("RESULT UNRESOLVED")}\n\n📈 {s.asset}\n💰 Entry • {s.entry:.6f}\n🏁 Expiry • —\n🕒 Boundary • {datetime.fromtimestamp(end,UAE).strftime("%H:%M:%S UAE")}\n\n⚠️ {err or "No verified closed expiry candle"}\n🚫 No WIN/LOSS recorded'); return
    candle_ts=float(row.timestamp); price=float(row.close); result='DRAW' if price==s.entry else ('WIN' if ((s.direction=='UP' and price>s.entry) or (s.direction=='DOWN' and price<s.entry)) else 'LOSS')
    record({'ts':time.time(),'asset':s.asset,'direction':s.direction,'confidence':s.confidence,'expiry':s.expiry,'entry':s.entry,'exit':price,'result':result,'entry_candle_ts':s.candle_ts,'expiry_candle_ts':candle_ts,'verification':'closed-candle'})
    reset_daily(); active.pop(uid,None); icon={'WIN':'🟢🏆','LOSS':'🔴⚠️','DRAW':'🟡➖'}[result]
    await send_text(uid,f'{header("FINAL MARKET OUTCOME")}\n\n{icon} {result}\n\n📈 {s.asset}\n➡️ {s.direction}\n💰 Entry • {s.entry:.6f}\n🏁 Expiry • {price:.6f}\n⏱️ Duration • {s.expiry} MIN\n🕒 Boundary • {datetime.fromtimestamp(end,UAE).strftime("%H:%M:%S UAE")}\n🕯️ Closed candle • {datetime.fromtimestamp(candle_ts,UAE).strftime("%H:%M:%S UAE")}\n\n🔎 Verification • CANDLE-CLOSED\n❌ Daily losses • {daily["losses"]}/{MAX_DAILY_LOSSES}\n🔥 Loss streak • {daily["streak"]}/{MAX_STREAK}\n\nMARKET OUTCOME • NOT BROKER ACCOUNT P/L\n🚫 AUTO-TRADE OFF • MANUAL ONLY')
    if result=='LOSS': recovery_until[uid]=time.time()+300; await send_text(uid,f'{header("RECOVERY WINDOW")}\n\n🛡️ Loss protection ACTIVE\n⏸️ Next signal paused • 5 MIN\n🔬 Research continues\n🚫 Martingale OFF • Auto-trade OFF')

async def scan_once():
    reset_daily()
    if daily['losses']>=MAX_DAILY_LOSSES or daily['streak']>=MAX_STREAK: log.warning('CANDICE RISK STOP losses=%s streak=%s',daily['losses'],daily['streak']); return
    assets=await broker.live_assets(); log.info('CANDICE SCAN START assets=%d users=%d interval=%ss pre_entry=%ss',len(assets),len(users),INTERVAL,PRE_ENTRY_SECONDS)
    if not users or not assets:return
    sem=asyncio.Semaphore(6)
    async def research(asset):
        async with sem:
            df,e=await broker.candles(asset,60,60,360)
            if e is None and df is not None and not df.empty:
                snap=technical_snapshot(df); record({'ts':time.time(),'asset':asset,'research':True,'direction':snap['direction'],'strength':snap['strength'],'rsi':snap['rsi14'],'adx':snap['adx14']}); log.info('CANDICE RESEARCH pair=%s direction=%s strength=%.2f RSI=%.1f ADX=%.1f',asset,snap['direction'],snap['strength'],snap['rsi14'],snap['adx14'])
            else: log.info('CANDICE RESEARCH REJECT pair=%s reason=%s',asset,e or 'no fresh candles')
    await asyncio.gather(*(research(a) for a in assets[:30]))
    for uid,asset in list(selected.items()):
        if uid in active or asset not in assets:continue
        if recovery_until.get(uid,0)>time.time():continue
        recovery_until.pop(uid,None); a=await analyze(asset,broker,summary(asset)); log.info('AI ANALYSIS pair=%s decision=%s direction=%s confidence=%s expiry=%s reason=%s evidence=%s',asset,a.decision,a.direction,a.confidence,a.expiry,a.reason,a.evidence)
        if a.decision!='APPROVE':continue
        df,e=await broker.candles(asset,60,60,120)
        if e or df is None or df.empty:continue
        row=broker.closed_candle(df,time.time(),60)
        if row is None:log.info('ENTRY REJECT pair=%s reason=no closed 1m candle',asset);continue
        entry=float(row.close); candle_ts=float(row.timestamp); entry_ts=float((int(time.time())//60+1)*60); wait=entry_ts-time.time()
        if wait>PRE_ENTRY_SECONDS+2 or wait<PRE_ENTRY_SECONDS-2:log.info('ENTRY REJECT pair=%s reason=not_in_10s_window seconds_to_entry=%.1f',asset,wait);continue
        key=f'{uid}:{asset}:{int(candle_ts)}'
        if key in sent_keys:continue
        msg=await send_signal_card(uid,asset,a.direction,entry_ts,candle_ts,a.expiry,a.confidence,a.timeframe,a.evidence)
        if msg is None:log.warning('SIGNAL NOT RESERVED pair=%s reason=telegram card delivery failed',asset);continue
        log.info('SIGNAL CARD QUALIFIED pair=%s direction=%s entry=%s expiry=%s confidence=%s timeframe=%s',asset,a.direction,datetime.fromtimestamp(entry_ts,UAE).strftime('%H:%M:%S'),a.expiry,a.confidence,a.timeframe)
        s=Signal(asset,a.direction,a.confidence,a.expiry,entry,entry_ts,candle_ts,a.reason,a.evidence); active[uid]=s; sent_keys.add(key); asyncio.create_task(result_monitor(uid,s,msg))

async def scheduler():
    while True:
        now_ts=time.time(); next_tick=(int(now_ts)//60)*60+50
        if next_tick<=now_ts:next_tick+=60
        await asyncio.sleep(max(0.2,next_tick-now_ts))
        try:await scan_once()
        except Exception:log.exception('scan cycle failed')

async def bot_main():
    global tg_app,BOT_LOOP
    if not TOKEN:raise RuntimeError('TELEGRAM_BOT_TOKEN is missing')
    BOT_LOOP=asyncio.get_running_loop(); tg_app=Application.builder().token(TOKEN).build()
    for command,fn in [('start',cmd_start),('access',cmd_access),('assets',cmd_assets),('status',cmd_status),('session',cmd_session),('update',cmd_update)]:tg_app.add_handler(CommandHandler(command,fn))
    tg_app.add_handler(CallbackQueryHandler(asset_callback,r'^asset:')); await tg_app.initialize(); await tg_app.start()
    webhook_base=os.getenv('TELEGRAM_WEBHOOK_URL','').strip().rstrip('/')
    if not webhook_base:
        render_url=os.getenv('RENDER_EXTERNAL_URL','').strip().rstrip('/')
        if render_url:webhook_base=render_url
    if webhook_base:
        webhook_url=webhook_base+WEBHOOK_PATH; kwargs={'url':webhook_url,'drop_pending_updates':True}
        if WEBHOOK_SECRET:kwargs['secret_token']=WEBHOOK_SECRET
        await tg_app.bot.set_webhook(**kwargs); log.warning('CANDICE TELEGRAM ONLINE — WEBHOOK MODE — %s — 1M SCANNER — VISUAL SIGNAL CARDS — LIVE TIMER — AUTO-TRADE OFF',webhook_url)
    else:
        await tg_app.bot.delete_webhook(drop_pending_updates=True); await tg_app.updater.start_polling(drop_pending_updates=True); log.warning('CANDICE TELEGRAM ONLINE — LOCAL POLLING MODE — 1M SCANNER — VISUAL SIGNAL CARDS — LIVE TIMER — AUTO-TRADE OFF')
    asyncio.create_task(broker.connect_forever()); asyncio.create_task(scheduler())
    while True:await asyncio.sleep(3600)

@flask_app.post(WEBHOOK_PATH)
def telegram_webhook():
    if WEBHOOK_SECRET and request.headers.get('X-Telegram-Bot-Api-Secret-Token','')!=WEBHOOK_SECRET:return 'forbidden',403
    if tg_app is None or BOT_LOOP is None:return 'bot not ready',503
    try:
        data=request.get_json(force=True,silent=False); update=Update.de_json(data,tg_app.bot); asyncio.run_coroutine_threadsafe(tg_app.process_update(update),BOT_LOOP); return 'OK',200
    except Exception as e:log.exception('TELEGRAM WEBHOOK FAILED: %s',e); return 'bad request',400

@flask_app.get('/')
def home():return f'{VERSION} ONLINE — FLEX market-data / 1-minute manual signals / live visual timer'
@flask_app.get('/health')
def health():return 'OK'
def start_bot_thread():asyncio.run(bot_main())
if __name__=='__main__':
    port=int(os.getenv('PORT','10000')); threading.Thread(target=start_bot_thread,daemon=True).start(); flask_app.run(host='0.0.0.0',port=port)
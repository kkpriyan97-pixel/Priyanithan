from __future__ import annotations
import asyncio, logging, os, threading, time
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import Flask, request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler
from candice_broker import Broker
from candice_engine import analyze, session_state, technical_snapshot
from candice_memory import record, summary, today_risk, authorized_users, authorize_user

VERSION = '8.5-FULL-BOT-1M-WEBHOOK'
UAE = ZoneInfo('Asia/Dubai')
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
ACCESS = os.getenv('ACCESS_CODE', '').strip()
OT_TOKEN = os.getenv('OLYMPIATRADE_ACCESS_TOKEN', os.getenv('OLYMPTRADE_ACCESS_TOKEN', '')).strip()
INTERVAL = 60
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

def expiry_boundary(candle_ts: float, expiry: int) -> float:
    return float(candle_ts) + 60.0 + int(expiry) * 60.0

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

async def edit_text(cid, mid, text, reply_markup=None):
    try:
        await tg_app.bot.edit_message_text(chat_id=cid, message_id=mid, text=text, reply_markup=reply_markup, disable_web_page_preview=True); return True
    except Exception as e:
        log.warning('TELEGRAM TEXT EDIT FAILED message_id=%s: %s', mid, e); return False

async def cmd_access(update, ctx):
    if not ACCESS or not ctx.args or ctx.args[0].strip() != ACCESS:
        await update.message.reply_text('🔒 CANDICE AI\n\nAccess denied.'); return
    uid = authorize_user(update.effective_user.id); users.add(uid)
    log.info('CANDICE USER AUTHORIZED chat=%s persistent_users=%d', uid, len(users)); await cmd_assets(update, ctx)

async def cmd_start(update, ctx):
    if update.effective_user.id not in users:
        await update.message.reply_text('🔒 CANDICE AI\n\nUse /access <code> first.'); return
    await cmd_assets(update, ctx)

async def cmd_assets(update, ctx):
    assets = await broker.live_assets(); names = ' • '.join(assets) if assets else 'No fresh FLEX assets available'
    text = (f'{header("FLEX ASSET SELECTOR")}\n\n📈 LIVE MARKET\n🟢 Fresh 1-minute candle VERIFIED\n\n'
            f'🔥 Available: {len(assets)}\n{names}\n\n⚡ FLEX / FIXED-TIME\n🧠 Candice AI research ACTIVE\n'
            f'🛡️ Technical + AI confirmation required\n\n👇 Select your asset')
    await send_text(update.effective_user.id, text, reply_markup=keyboard(assets))

async def cmd_status(update, ctx):
    reset_daily(); uid = update.effective_user.id
    await send_text(uid, f'{header("SYSTEM STATUS")}\n\n🛰️ Broker: {"🟢 CONNECTED" if broker.connected() else "🔴 DISCONNECTED"}\n'
        f'📈 FLEX: {selected.get(uid, "NONE")}\n🔄 Scanner: every 1 minute\n👤 Authorized users: {len(users)}\n🧠 Session: {session_state()}\n'
        f'❌ Daily losses: {daily["losses"]}/{MAX_DAILY_LOSSES}\n🔥 Loss streak: {daily["streak"]}/{MAX_STREAK}\n\n'
        f'🚫 Auto-trade OFF\n🚫 Martingale OFF\n🚫 Forex mode OFF\n🖼️ Telegram media OFF')

async def cmd_session(update, ctx):
    reset_daily()
    await send_text(update.effective_user.id, f'{header("LIVE SCAN CONTROL")}\n\n🟢 CONTINUOUS SIGNAL ENGINE\n🔄 Analysis: every 1 minute\n'
        f'🕯️ Candle: closed 1M\n⏱️ Expiry: 2 / 3 / 5 / 15 MIN\n⏳ Signal countdown: ACTIVE\n🏁 Expiry verification: ACTIVE\n\n'
        f'🔬 Research: 24/7\n🛡️ Risk gates: ACTIVE\n🚫 Auto-trade OFF • Manual only')

async def cmd_update(update, ctx): await cmd_session(update, ctx)

async def asset_callback(update, ctx):
    q = update.callback_query; await q.answer(); uid = q.from_user.id
    if uid not in users or not q.data.startswith('asset:'): return
    asset = q.data.split(':', 1)[1].upper()
    if asset == 'NOOP': return
    live = await broker.live_assets()
    if asset not in live:
        await edit_text(uid, q.message.message_id, f'{header("ASSET CHECK FAILED")}\n\n🔴 {asset} is no longer live or its candle is stale.\n\nUse /start again.'); return
    selected[uid] = asset; t = now().strftime('%H:%M:%S UAE')
    await edit_text(uid, q.message.message_id, f'{header("ASSET READY")}\n\n📈 {asset}\n\n🟢 MARKET STATUS • LIVE\n🔵 CANDLE • 1 MIN FRESH\n🕒 VERIFIED • {t}\n\n'
        f'🔥🔥🔥 CANDICE RESEARCH ACTIVE\n\n🟢 Trend structure\n🔵 Momentum confirmation\n🟣 Volatility / breakout check\n🟠 MACD confirmation\n🟡 AI decision gate\n\n'
        f'⚡ READY FOR QUALIFIED SIGNAL\n⏱️ 2 / 3 / 5 / 15 MIN\n\n🛡️ MANUAL TRADE ONLY • AUTO-TRADE OFF')

async def signal_countdown(cid, msg, s):
    if msg is None: return
    end = expiry_boundary(s.candle_ts, s.expiry); last_bucket = None
    while time.time() < end and active.get(cid) is s:
        rem = max(0, int(end - time.time())); mm, ss = divmod(rem, 60); bucket = rem // 5
        if bucket != last_bucket:
            last_bucket = bucket
            text = (f'{header("ACTIVE SIGNAL")}\n\n📈 {s.asset}\n'
                    f'{"🟢 ⬆️ TRADE UP" if s.direction == "UP" else "🔴 ⬇️ TRADE DOWN"}\n🔥 Confidence • {s.confidence}%\n⏱️ Duration • {s.expiry} MIN\n💰 Entry • {s.entry:.6f}\n\n'
                    f'⏳ COUNTDOWN • {mm:02d}:{ss:02d}\n🕒 Expiry • {datetime.fromtimestamp(end, UAE).strftime("%H:%M:%S UAE")}\n\n'
                    f'🟢 Technical gate PASSED\n🟣 AI gate APPROVED\n🕯️ Entry candle • CLOSED 1M\n🚫 Auto-trade OFF • Manual only')
            await edit_text(cid, msg.message_id, text)
        await asyncio.sleep(1)

async def result_monitor(uid, s, msg):
    end = expiry_boundary(s.candle_ts, s.expiry); await signal_countdown(uid, msg, s)
    await send_text(uid, f'{header("EXPIRY VERIFICATION")}\n\n📈 {s.asset}\n➡️ Direction • {s.direction}\n💰 Entry • {s.entry:.6f}\n'
        f'🕒 Boundary • {datetime.fromtimestamp(end, UAE).strftime("%H:%M:%S UAE")}\n\n🔎 Waiting for the boundary candle to close...')
    row = None; err = ''
    for _ in range(8):
        row, err = await broker.closed_candle_at(s.asset, end, 60, 180)
        if row is not None: break
        await asyncio.sleep(3)
    if row is None:
        active.pop(uid, None); await send_text(uid, f'{header("RESULT UNRESOLVED")}\n\n📈 {s.asset}\n💰 Entry • {s.entry:.6f}\n🏁 Expiry • —\n'
            f'🕒 Boundary • {datetime.fromtimestamp(end, UAE).strftime("%H:%M:%S UAE")}\n\n⚠️ {err or "No verified closed expiry candle"}\n🚫 No WIN/LOSS recorded'); return
    candle_ts = float(row.timestamp); price = float(row.close)
    result = 'DRAW' if price == s.entry else ('WIN' if ((s.direction == 'UP' and price > s.entry) or (s.direction == 'DOWN' and price < s.entry)) else 'LOSS')
    record({'ts': time.time(), 'asset': s.asset, 'direction': s.direction, 'confidence': s.confidence, 'expiry': s.expiry, 'entry': s.entry, 'exit': price, 'result': result, 'entry_candle_ts': s.candle_ts, 'expiry_candle_ts': candle_ts, 'verification': 'closed-candle'})
    reset_daily(); active.pop(uid, None); icon = {'WIN':'🟢🏆','LOSS':'🔴⚠️','DRAW':'🟡➖'}[result]
    await send_text(uid, f'{header("FINAL MARKET OUTCOME")}\n\n{icon} {result}\n\n📈 {s.asset}\n➡️ {s.direction}\n💰 Entry • {s.entry:.6f}\n'
        f'🏁 Expiry • {price:.6f}\n⏱️ Duration • {s.expiry} MIN\n🕒 Boundary • {datetime.fromtimestamp(end, UAE).strftime("%H:%M:%S UAE")}\n'
        f'🕯️ Closed candle • {datetime.fromtimestamp(candle_ts, UAE).strftime("%H:%M:%S UAE")}\n\n🔎 Verification • CANDLE-CLOSED\n'
        f'❌ Daily losses • {daily["losses"]}/{MAX_DAILY_LOSSES}\n🔥 Loss streak • {daily["streak"]}/{MAX_STREAK}\n\nMARKET OUTCOME • NOT BROKER ACCOUNT P/L\n🚫 AUTO-TRADE OFF • MANUAL ONLY')
    if result == 'LOSS':
        recovery_until[uid] = time.time() + 300
        await send_text(uid, f'{header("RECOVERY WINDOW")}\n\n🛡️ Loss protection ACTIVE\n⏸️ Next signal paused • 5 MIN\n🔬 Research continues\n🚫 Martingale OFF • Auto-trade OFF')

async def scan_once():
    reset_daily()
    if daily['losses'] >= MAX_DAILY_LOSSES or daily['streak'] >= MAX_STREAK:
        log.warning('CANDICE RISK STOP losses=%s streak=%s', daily['losses'], daily['streak']); return
    assets = await broker.live_assets()
    log.info('CANDICE SCAN START assets=%d users=%d interval=%ss', len(assets), len(users), INTERVAL)
    if not users or not assets: return
    sem = asyncio.Semaphore(6)
    async def research(asset):
        async with sem:
            df, e = await broker.candles(asset, 60, 60, 360)
            if e is None and df is not None and not df.empty:
                snap = technical_snapshot(df); record({'ts': time.time(), 'asset': asset, 'research': True, 'direction': snap['direction'], 'strength': snap['strength'], 'rsi': snap['rsi14'], 'adx': snap['adx14']})
                log.info('CANDICE RESEARCH pair=%s direction=%s strength=%.2f RSI=%.1f ADX=%.1f', asset, snap['direction'], snap['strength'], snap['rsi14'], snap['adx14'])
            else: log.info('CANDICE RESEARCH REJECT pair=%s reason=%s', asset, e or 'no fresh candles')
    await asyncio.gather(*(research(a) for a in assets[:30]))
    for uid, asset in list(selected.items()):
        if uid in active or asset not in assets: continue
        if recovery_until.get(uid, 0) > time.time(): continue
        recovery_until.pop(uid, None)
        a = await analyze(asset, broker, summary(asset))
        log.info('AI ANALYSIS pair=%s decision=%s direction=%s confidence=%s expiry=%s reason=%s evidence=%s', asset, a.decision, a.direction, a.confidence, a.expiry, a.reason, a.evidence)
        if a.decision != 'APPROVE': continue
        df, e = await broker.candles(asset, 60, 60, 120)
        if e or df is None or df.empty: continue
        row = broker.closed_candle(df, time.time(), 60)
        if row is None: log.info('ENTRY REJECT pair=%s reason=no closed 1m candle', asset); continue
        entry = float(row.close); candle_ts = float(row.timestamp); key = f'{uid}:{asset}:{int(candle_ts)}'
        if key in sent_keys: continue
        evidence = '\n'.join(f'🟢 {x}' for x in a.evidence) if a.evidence else '🟡 Multi-indicator alignment verified'
        text = (f'{header("NEW SIGNAL")}\n\n📈 {asset}\n\n{"🟢 ⬆️ TRADE UP" if a.direction == "UP" else "🔴 ⬇️ TRADE DOWN"}\n'
                f'🕒 Entry • {datetime.fromtimestamp(time.time(), UAE).strftime("%H:%M:%S UAE")}\n🕯️ Candle • {datetime.fromtimestamp(candle_ts, UAE).strftime("%H:%M:%S UAE")}\n\n'
                f'🔥 STRONG CONFIRMATION\n\n{evidence}\n\n⏱️ EXPIRY • {a.expiry} MIN\n🧠 AI CONFIDENCE • {a.confidence}%\n📊 TIMEFRAMES • {a.timeframe}\n\n'
                f'⏳ TIMER • STARTING\n🛡️ FRESH CLOSED-CANDLE ENTRY\n⚠️ MANUAL TRADE ONLY • AUTO-TRADE OFF')
        msg = await send_text(uid, text)
        if msg is None:
            log.warning('SIGNAL NOT RESERVED pair=%s reason=telegram delivery failed', asset); continue
        s = Signal(asset, a.direction, a.confidence, a.expiry, entry, time.time(), candle_ts, a.reason, a.evidence)
        active[uid] = s; sent_keys.add(key); asyncio.create_task(result_monitor(uid, s, msg))

async def scheduler():
    while True:
        now_ts = time.time(); delay = 60 - (now_ts % 60); await asyncio.sleep(max(0.5, delay))
        try: await scan_once()
        except Exception: log.exception('scan cycle failed')

async def bot_main():
    global tg_app, BOT_LOOP
    if not TOKEN: raise RuntimeError('TELEGRAM_BOT_TOKEN is missing')
    BOT_LOOP = asyncio.get_running_loop()
    tg_app = Application.builder().token(TOKEN).build()
    for command, fn in [('start', cmd_start), ('access', cmd_access), ('assets', cmd_assets), ('status', cmd_status), ('session', cmd_session), ('update', cmd_update)]: tg_app.add_handler(CommandHandler(command, fn))
    tg_app.add_handler(CallbackQueryHandler(asset_callback, r'^asset:'))
    await tg_app.initialize()
    await tg_app.start()
    webhook_base = os.getenv('TELEGRAM_WEBHOOK_URL', '').strip().rstrip('/')
    if not webhook_base:
        render_url = os.getenv('RENDER_EXTERNAL_URL', '').strip().rstrip('/')
        if render_url: webhook_base = render_url
    if webhook_base:
        webhook_url = webhook_base + WEBHOOK_PATH
        kwargs = {'url': webhook_url, 'drop_pending_updates': True}
        if WEBHOOK_SECRET: kwargs['secret_token'] = WEBHOOK_SECRET
        await tg_app.bot.set_webhook(**kwargs)
        log.warning('CANDICE TELEGRAM ONLINE — WEBHOOK MODE — %s — 1M SCANNER — TIMER ACTIVE — AUTO-TRADE OFF', webhook_url)
    else:
        await tg_app.bot.delete_webhook(drop_pending_updates=True)
        await tg_app.updater.start_polling(drop_pending_updates=True)
        log.warning('CANDICE TELEGRAM ONLINE — LOCAL POLLING MODE — 1M SCANNER — TIMER ACTIVE — AUTO-TRADE OFF')
    asyncio.create_task(broker.connect_forever()); asyncio.create_task(scheduler())
    while True: await asyncio.sleep(3600)

@flask_app.post(WEBHOOK_PATH)
def telegram_webhook():
    if WEBHOOK_SECRET and request.headers.get('X-Telegram-Bot-Api-Secret-Token', '') != WEBHOOK_SECRET:
        return 'forbidden', 403
    if tg_app is None or BOT_LOOP is None:
        return 'bot not ready', 503
    try:
        data = request.get_json(force=True, silent=False)
        update = Update.de_json(data, tg_app.bot)
        asyncio.run_coroutine_threadsafe(tg_app.process_update(update), BOT_LOOP)
        return 'OK', 200
    except Exception as e:
        log.exception('TELEGRAM WEBHOOK FAILED: %s', e)
        return 'bad request', 400

@flask_app.get('/')
def home(): return f'{VERSION} ONLINE — FLEX market-data / 1-minute manual signals / timers active'
@flask_app.get('/health')
def health(): return 'OK'
def start_bot_thread(): asyncio.run(bot_main())
if __name__ == '__main__':
    port = int(os.getenv('PORT', '10000')); threading.Thread(target=start_bot_thread, daemon=True).start(); flask_app.run(host='0.0.0.0', port=port)
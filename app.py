from __future__ import annotations
import asyncio, logging, os, threading, time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler
from candice_broker import Broker
from candice_engine import analyze, session_state, technical_snapshot
from candice_memory import record, summary, today_risk
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
def text_payload(kind,asset='ASIA_X',direction='',confidence=0,expiry=0,reason='',caption='',**kwargs):
 lines=[]
 if caption: lines.append(caption)
 if kind=='signal':
  lines += [f'📡 CANDICE AI SIGNAL • {asset}', f'📈 Direction: {direction}', f'🎯 Confidence: {confidence}%', f'⏱ Duration: {expiry} MIN', f'💰 Entry: {kwargs.get("entry","—")}', f'🕐 Signal: {kwargs.get("signal_time","—")}', f'🏁 Expiry: {kwargs.get("expiry_time","—")}', '🔎 Technical gate: PASSED', '⚠️ MANUAL TRADE ONLY • AUTO TRADE OFF']
 elif kind=='result':
  lines += [f'📊 FINAL MARKET OUTCOME • {asset}', f'{"✅" if direction=="WIN" else "❌" if direction=="LOSS" else "➖
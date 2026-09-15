"""Candice runtime overlay: continuous real 1m research, bounded 5m delivery window, read-only only."""
from __future__ import annotations
import json, logging, os, sys, threading, time, requests
LOG=logging.getLogger("candice.runtime")
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","").strip(); CONFIGURED_CHAT=os.getenv("TELEGRAM_CHAT_ID","").strip(); ACCESS_CODE=os.getenv("CANDICE_ACCESS_CODE","").strip()
LOCK=threading.RLock(); ACTIVE_CHAT=""; AUTHORIZED=False; BOT_ID=""; POLL_STARTED=False; INSTALLED=False
CANDIDATES={}; SENT_WINDOWS=set(); LIVE_RECEIPTS={}; LAST_SIGNAL={}; SESSION=requests.Session(); EXPIRIES={2,3,5,15}
MODE="READ_ONLY_DEMO"; BALANCE_TEXT="NOT_AVAILABLE (read-only market feed does not expose account balance)"

def tg_send(chat,text):
    if not TOKEN or not chat:return False
    try:
        r=SESSION.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",json={"chat_id":chat,"text":text,"disable_web_page_preview":True},timeout=15)
        if r.ok:return True
        try:desc=r.json().get("description","unknown")
        except Exception:desc="unknown"
        LOG.warning("Telegram send failed HTTP=%s | %s",r.status_code,desc);return False
    except Exception as e:
        LOG.warning("Telegram send failed %s",type(e).__name__);return False

def verify_bot():
    global BOT_ID
    if not TOKEN:return False
    try:
        r=SESSION.get(f"https://api.telegram.org/bot{TOKEN}/getMe",timeout=15);r.raise_for_status();u=r.json().get("result") or {}
        BOT_ID=str(u.get("id","")).strip();LOG.info("TELEGRAM_BOT_ID verified=%s username=%s",BOT_ID,u.get("username",""))
        if CONFIGURED_CHAT and CONFIGURED_CHAT==BOT_ID:LOG.error("TELEGRAM_CHAT_CONFIG_INVALID configured chat is bot itself")
        return bool(BOT_ID)
    except Exception as e:LOG.error("Telegram getMe failed: %s",type(e).__name__);return False

def human_update(u):
    m=u.get("message") or {};chat=m.get("chat") or {};sender=m.get("from") or {};cid=str(chat.get("id","")).strip();typ=str(chat.get("type","")).lower()
    return bool(cid and typ in ("private","group","supergroup") and not sender.get("is_bot") and cid!=BOT_ID)

def handle_update(u):
    global ACTIVE_CHAT,AUTHORIZED
    m=u.get("message") or {};cid=str((m.get("chat") or {}).get("id","")).strip();text=str(m.get("text","")).strip();low=text.lower()
    if not cid or not text:return
    if not human_update(u):LOG.warning("TELEGRAM_UPDATE_IGNORED chat=%s",cid);return
    if low.startswith("/start"):
        with LOCK:ACTIVE_CHAT,AUTHORIZED=cid,True
        LOG.info("TELEGRAM_OWNER_AUTHORIZED chat=%s source=/start",cid)
        tg_send(cid,"🎯 CANDICE AI\n\n✅ ONLINE\n🔐 ACCESS • AUTHORIZED\n📡 MARKET • READ-ONLY\n🚫 AUTO-TRADE • OFF\n🚫 MARTINGALE • OFF\n\n/status • connection\n/performance • session");return
    if low.startswith("/access "):
        ok=bool(ACCESS_CODE) and text.split(" ",1)[1].strip().casefold()==ACCESS_CODE.casefold()
        if ok:
            with LOCK:ACTIVE_CHAT,AUTHORIZED=cid,True
        LOG.info("TELEGRAM_ACCESS_RESULT chat=%s authorized=%s",cid,ok);tg_send(cid,"🎯 CANDICE AI\n\n✅ ONLINE\n🔐 ACCESS • AUTHORIZED" if ok else "❌ ACCESS • DENIED");return
    with LOCK:allowed=AUTHORIZED and cid==ACTIVE_CHAT
    if not allowed:return
    mod=sys.modules.get("__main__")
    if low.startswith("/status"):
        try:
            s=mod.live_feed.status() if getattr(mod,"live_feed",None) else {}
            tg_send(cid,f"🎯 CANDICE STATUS\n\n📡 Olymp • {'🟢 CONNECTED' if s.get('connected') else '🔴 DISCONNECTED'}\n📈 Tradeable assets • {len(s.get('tradeable_assets') or [])}\n🕐 Real 1m candles • ON\n🧠 Brain • ON\n📊 Continuous 1m scan • ON\n🛡️ READ-ONLY • YES\n🚫 Auto-trade • OFF\n🚫 Martingale • OFF\n💰 Account balance • {BALANCE_TEXT}")
        except Exception:tg_send(cid,"🔴 Status temporarily unavailable")
    elif low.startswith("/performance"):
        try:
            from outcome_engine import performance
            p=performance();tg_send(cid,f"📊 CANDICE PERFORMANCE\n\nSignals evaluated • {p['evaluated']}\nWins • {p['wins']}\nLosses • {p['losses']}\nTies • {p['ties']}\nWin rate • {p['win_rate']}%")
        except Exception:tg_send(cid,"📊 Performance is not ready yet.")

def telegram_poller():
    global POLL_STARTED
    with LOCK:
        if POLL_STARTED:return
        POLL_STARTED=True
    if not TOKEN:LOG.warning("Telegram poller disabled: token missing");return
    verify_bot();offset=0;LOG.info("TELEGRAM_OWNER_POLLER started | configured_chat=%s",CONFIGURED_CHAT or "<none>")
    while True:
        try:
            r=SESSION.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",params={"timeout":25,"offset":offset+1,"allowed_updates":json.dumps(["message"])},timeout=35)
            if r.status_code in (409,429):time.sleep(10);continue
            if r.status_code==401:LOG.error("Telegram token rejected");time.sleep(30);continue
            r.raise_for_status()
            for u in r.json().get("result",[]):
                offset=max(offset,int(u.get("update_id",offset)))
                try:handle_update(u)
                except Exception:LOG.exception("Telegram update handling failed")
        except Exception:time.sleep(5)

def candle_age(c,asset=None):
    now=time.time()
    if asset:
        with LOCK:rec=LIVE_RECEIPTS.get(asset)
        if rec and rec[1]<=now+5:return max(0,now-rec[1])
    try:r=float(c.get("received_at",0));return max(0,now-r) if 0<r<=now+5 else 10**9
    except Exception:return 10**9

def _install_runtime():
    global INSTALLED
    while not INSTALLED:
        mod=sys.modules.get("__main__")
        if mod is None or not all(hasattr(mod,n) for n in ("analyze","on_olymp_candle","send_signal","candles")):
            time.sleep(.2);continue
        try:
            from strategy_brain import evaluate as brain_evaluate
            from outcome_engine import on_candle as outcome_on_candle, register as outcome_register
            original_candle=mod.on_olymp_candle;original_send=mod.send_signal;original_telegram=getattr(mod,"telegram",None)
            def brain_analyze(data):
                asset=getattr(threading.current_thread(),"candice_asset",None) or "UNKNOWN"
                b=brain_evaluate(asset,data,{"mtf":{"1m":0,"3m":0,"5m":0}})
                if b.get("allow"):return {"decision":"SIGNAL","direction":b["direction"],"confidence":b["score"],"reasons":b.get("reasons",[]),"brain":b,"mtf":{},"indicators":{}}
                return {"decision":"NO_SIGNAL","direction":b.get("direction"),"confidence":b.get("score",0),"reasons":b.get("reasons",[]),"brain":b,"mtf":{},"indicators":{}}
            def assets():
                try:return set((getattr(mod,"live_feed",None).status() or {}).get("tradeable_assets") or [])
                except Exception:return set()
            def remember_candidate(asset,data,tech,source):
                if tech.get("decision")!="SIGNAL":return
                b=tech.get("brain") or {};w=int(time.time()//300);key=f"{asset}:{w}"
                with LOCK:CANDIDATES[key]={"asset":asset,"window":w,"created":time.time(),"quality":int(b.get("score",tech.get("confidence",0))),"expiry":int(b.get("expiry",5) or 5),"direction":b.get("direction",tech.get("direction")),"brain":b,"source":source}
                LOG.info("BRAIN_SIGNAL_CANDIDATE asset=%s window=%s direction=%s confidence=%s source=%s fresh_age=%.1fs",asset,w,tech.get("direction"),tech.get("confidence"),source,candle_age(data[-1],asset))
            def capture_send(asset,data,tech,expiry=5):remember_candidate(asset,data,tech,"candle_callback");return {"ok":True,"status":"DEFERRED"}
            def decorated_telegram(text):
                extra=""
                if "CANDICE AI • LIVE MARKET" in text:
                    asset=text.split("📈 Asset • ",1)[1].split("\n",1)[0] if "📈 Asset • " in text else ""
                    with LOCK:ctx=LAST_SIGNAL.get(asset)
                    if ctx:
                        prev,sig=ctx.get("previous"),ctx.get("signal");fmt=lambda c:f"O {c['open']:.6f} | H {c['high']:.6f} | L {c['low']:.6f} | C {c['close']:.6f}" if c else "NOT_AVAILABLE"
                        extra=f"\n\n🕯️ PREVIOUS 1M CANDLE\n{fmt(prev)}\n🕯️ SIGNAL 1M CANDLE\n{fmt(sig)}\n💳 Account mode • {MODE}\n💰 Account balance • {BALANCE_TEXT}\n🔎 Verification • fresh live candle + read-only market feed"
                elif "CANDICE AI • TRADE RESULT" in text:extra=f"\n💳 Account mode • {MODE}\n💰 Account balance • {BALANCE_TEXT}\n🔎 Verification • result calculated from completed live candle"
                with LOCK:target=ACTIVE_CHAT if AUTHORIZED else ""
                if not target:LOG.warning("Telegram delivery blocked: no authorized human chat");return False
                if BOT_ID and target==BOT_ID:LOG.error("Telegram destination is bot id; blocked");return False
                ok=tg_send(target,text+extra);LOG.info("TELEGRAM_SIGNAL_DELIVERED chat=%s",target) if ok else LOG.error("TELEGRAM_SIGNAL_FAILED chat=%s",target);return ok
            def wrapped_candle(asset,candle):
                c=dict(candle);c.setdefault("received_at",time.time())
                with LOCK:LIVE_RECEIPTS[asset]=(float(c.get("timestamp",time.time())),float(c["received_at"]))
                try:outcome_on_candle(asset,c,decorated_telegram)
                except Exception:LOG.exception("Outcome processing failed asset=%s",asset)
                threading.current_thread().candice_asset=asset;original_candle(asset,c)
            mod.analyze=brain_analyze;mod.send_signal=capture_send
            if original_telegram:mod.telegram=decorated_telegram
            mod.on_olymp_candle=wrapped_candle
            def scheduler():
                LOG.info("BRAIN_SCHEDULER started: real 1m research; 5m windows; signal inside current window")
                last_min=-1;last_window=-1;last_try={}
                while True:
                    try:
                        now=time.time();w=int(now//300);minute=int(now//60);sec=int(now)%300
                        if w!=last_window:
                            last_window=w;LOG.info("BRAIN_WINDOW_OPEN window=%s | delivery_window=%s-%s",w,time.strftime('%H:%M',time.localtime(w*300)),time.strftime('%H:%M',time.localtime(w*300+299)))
                        if minute!=last_min and int(now)%60>=8:
                            last_min=minute;allowed=assets();with_lock=None
                            with LOCK:snapshot={a:list(v) for a,v in mod.candles.items() if a in allowed}
                            scanned=fresh=qualified=0;top=[]
                            for asset,data in snapshot.items():
                                if len(data)<60:continue
                                scanned+=1;age=candle_age(data[-1],asset)
                                if age>75:continue
                                fresh+=1
                                try:
                                    threading.current_thread().candice_asset=asset;tech=brain_analyze(data)
                                    if tech.get("decision")=="SIGNAL":qualified+=1;top.append((int(tech.get("confidence",0)),asset,tech.get("direction"),age));remember_candidate(asset,data,tech,"minute_scan")
                                except Exception:LOG.exception("Research failed asset=%s",asset)
                            top.sort(reverse=True);rank=" | ".join(f"#{i+1} {a} {d} {c}% age={age:.1f}s" for i,(c,a,d,age) in enumerate(top[:5])) or "No qualified setup in this minute"
                            LOG.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");LOG.info("🧠 CANDICE BRAIN • 1-MIN RESEARCH | minute=%s",minute);LOG.info("📊 FLEX assets available=%s | scanned=%s | fresh verified=%s | qualified=%s",len(allowed),scanned,fresh,qualified);LOG.info("🏆 TOP FLEX RANKING | %s",rank);LOG.info("🕯️ REAL 1M CANDLE RESEARCH | live completed candles only | freshness cutoff=75s");LOG.info("⏱️ 15→19 RULE | window=%s | current offset=%ss | deadline=%s",w,sec,time.strftime('%H:%M:%S',time.localtime(w*300+299)));LOG.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                        if AUTHORIZED and w not in SENT_WINDOWS and sec>=8 and now-last_try.get(w,0)>=2:
                            last_try[w]=now
                            with LOCK:pool=[v for v in CANDIDATES.values() if v.get("window")==w and v.get("asset") in assets()]
                            ranked=[]
                            for cand in pool:
                                asset=cand["asset"]
                                with LOCK:data=list(mod.candles.get(asset,[]))
                                if len(data)<60 or candle_age(data[-1],asset)>75:continue
                                threading.current_thread().candice_asset=asset;freshtech=brain_analyze(data)
                                if freshtech.get("decision")=="SIGNAL":ranked.append((int(freshtech.get("confidence",0)),asset,data,freshtech,cand))
                            if ranked:
                                ranked.sort(key=lambda x:(x[0],x[4].get("created",0)),reverse=True);_,asset,data,tech,cand=ranked[0];expiry=int((tech.get("brain") or {}).get("expiry",cand.get("expiry",5)) or 5);expiry=expiry if expiry in EXPIRIES else 5
                                with LOCK:LAST_SIGNAL[asset]={"previous":data[-2] if len(data)>1 else None,"signal":data[-1],"time":time.time()}
                                result=original_send(asset,data,tech,expiry)
                                if result.get("status")=="SIGNAL":
                                    with LOCK:SENT_WINDOWS.add(w)
                                    b=tech.get("brain") or {}
                                    try:outcome_register({"status":"SIGNAL","asset":asset,"direction":tech.get("direction"),"expiry":expiry,"confidence":tech.get("confidence",0),"timestamp":float(data[-1].get("timestamp",time.time())),"entry":float(data[-1].get("close",0)),"strategy":b.get("strategy","market_brain"),"regime":b.get("regime",""),"pattern":b.get("pattern",""),"features":b})
                                    except Exception:LOG.exception("Outcome registration failed asset=%s",asset)
                                    LOG.info("WINDOW_SIGNAL_15_TO_19 window=%s asset=%s direction=%s expiry=%s confidence=%s fresh_age=%.1fs",w,asset,tech.get("direction"),expiry,tech.get("confidence"),candle_age(data[-1],asset))
                            elif sec>=240:LOG.warning("15_TO_19_DEADLINE_NO_VALID_SETUP window=%s | no fresh qualified setup at deadline",w)
                        with LOCK:
                            for k,v in list(CANDIDATES.items()):
                                if v.get("window",-999)<w-12:CANDIDATES.pop(k,None)
                            for a,(ts,rec) in list(LIVE_RECEIPTS.items()):
                                if now-rec>180:LIVE_RECEIPTS.pop(a,None)
                        time.sleep(1)
                    except Exception:LOG.exception("BRAIN_SCHEDULER_ERROR");time.sleep(2)
            threading.Thread(target=scheduler,name="candice-brain-scheduler",daemon=True).start();INSTALLED=True;LOG.info("CANDICE_BRAIN_OVERLAY installed | 15_TO_19=ON | TELEGRAM_HUMAN_ROUTING=ON | ACCOUNT_BALANCE=READ_ONLY_UNAVAILABLE")
        except Exception:LOG.exception("Brain overlay install failed");time.sleep(2)
threading.Thread(target=telegram_poller,name="candice-telegram-owner",daemon=True).start()
threading.Thread(target=_install_runtime,name="candice-runtime-installer",daemon=True).start()

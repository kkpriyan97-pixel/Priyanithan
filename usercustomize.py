"""Candice verification overlay: account/candle telemetry and active 15->19 fallback."""
from __future__ import annotations
import logging, sys, threading, time

LOG = logging.getLogger("candice.verify")
LOCK = threading.RLock()
ACCOUNT = {"mode":"UNKNOWN","balance":None,"currency":"","updated":0.0}
HOOKED = False
WRAPPED = False
FRESHNESS_PATCHED = False

try:
    import freshness_patch
except Exception:
    LOG.exception("Freshness bridge import failed")


def _fmt(v):
    try: return f"{float(v):.6f}"
    except Exception: return "-"


def _extract(msg):
    out=[]
    def walk(x):
        if isinstance(x,dict):
            d={str(k).lower():v for k,v in x.items()}
            bal=None
            for k in ("balance","amount","cash","equity","available_balance"):
                try:
                    if k in d: bal=float(d[k]); break
                except Exception: pass
            group=str(d.get("group",d.get("account_group",d.get("type","")))).lower()
            cur=str(d.get("currency",d.get("currency_code",""))).upper()
            if bal is not None: out.append((group,bal,cur))
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(msg)
    return out


async def _balance_cb(msg):
    vals=_extract(msg)
    if not vals: return
    with LOCK:
        for group,bal,cur in vals:
            if group in ("demo","real"):
                ACCOUNT.update(mode=group.upper(),balance=bal,currency=cur,updated=time.time())
                LOG.info("ACCOUNT_BALANCE_UPDATE mode=%s balance=%s currency=%s",group.upper(),bal,cur or "-")
                return
        group,bal,cur=vals[-1]
        ACCOUNT.update(mode=group.upper() if group else "UNKNOWN",balance=bal,currency=cur,updated=time.time())
        LOG.info("ACCOUNT_BALANCE_UPDATE mode=%s balance=%s currency=%s",ACCOUNT["mode"],bal,cur or "-")


def _account_text():
    with LOCK: a=dict(ACCOUNT)
    if a["balance"] is None: return "💳 Account • balance not received yet"
    return f"💳 Account • {a['mode']}\n💰 Balance • {a['balance']:.2f} {a['currency'] or ''}".rstrip()


def _candle_text(asset,data):
    if len(data)<2: return "🕯️ Previous 1m candle • unavailable"
    p,c=data[-2],data[-1]
    return ("🕯️ Previous 1m candle\n"
            f"   O {_fmt(p.get('open'))}  H {_fmt(p.get('high'))}  L {_fmt(p.get('low'))}  C {_fmt(p.get('close'))}\n"
            "🕯️ Signal candle\n"
            f"   O {_fmt(c.get('open'))}  H {_fmt(c.get('high'))}  L {_fmt(c.get('low'))}  C {_fmt(c.get('close'))}")


def _patch_candle_age(mod):
    global FRESHNESS_PATCHED
    if FRESHNESS_PATCHED: return
    def normalize_ts(value):
        try: ts=float(value)
        except Exception: return None
        if ts>1e11: ts/=1000.0
        return ts if 0<ts<=time.time()+5 else None
    def candle_age(c,asset=None):
        now=time.time(); ages=[]
        if asset:
            try:
                with mod.LOCK: rec=mod.LIVE_RECEIPTS.get(asset)
                if rec:
                    ts=normalize_ts(rec[1])
                    if ts is not None: ages.append(max(0.0,now-ts))
            except Exception: pass
        if isinstance(c,dict):
            for key in ("received_at","timestamp","t","time"):
                ts=normalize_ts(c.get(key))
                if ts is not None: ages.append(max(0.0,now-ts))
        return min(ages) if ages else 10**9
    mod.candle_age=candle_age
    FRESHNESS_PATCHED=True
    LOG.info("CANDICE_DELIVERY_FRESHNESS patched at verified startup | receipt + received_at + candle timestamp")


def _install_hooks(mod):
    global HOOKED, WRAPPED
    feed=getattr(mod,"live_feed",None)
    if not feed: return False
    sc=sys.modules.get("sitecustomize")
    if sc is not None: _patch_candle_age(sc)
    client=getattr(feed,"client",None)
    if client is not None and not HOOKED:
        client.register_callback(55,_balance_cb)
        HOOKED=True
        LOG.info("ACCOUNT_TELEMETRY_HOOK installed event=55")
    current=getattr(mod,"telegram",None)
    if current and not WRAPPED:
        def verified_telegram(text):
            t=str(text)
            if "🎯 CANDICE AI • LIVE MARKET" in t and "🟢 SIGNAL" in t:
                asset=next((line.split("•",1)[1].strip() for line in t.splitlines() if line.startswith("📈 Asset • ")),"")
                data=list(getattr(mod,"candles",{}).get(asset,[])) if asset else []
                t += "\n"+_candle_text(asset,data)+"\n"+_account_text()+"\n🔎 Verification • LIVE candle + account telemetry"
            elif "🎯 CANDICE AI • TRADE RESULT" in t:
                t += "\n"+_account_text()+"\n🔎 Verification • outcome + account telemetry"
            return current(t)
        verified_telegram._candice_verify_wrapped=True
        mod.telegram=verified_telegram
        WRAPPED=True
        LOG.info("SIGNAL_RESULT_TELEMETRY installed | candle_snapshot=ON | account=ON")
    return HOOKED and WRAPPED


def _send_fallback(mod,asset,data,tech,slot):
    sc=sys.modules.get("sitecustomize")
    sent_windows=getattr(sc,"SENT_WINDOWS",set()) if sc else set()
    if slot in sent_windows:
        LOG.info("15_TO_19_SKIP already_sent window=%s",slot); return
    expiry=int((tech.get("brain") or {}).get("expiry",5) or 5)
    if expiry not in (2,3,5,15): expiry=5
    entry=float(data[-1].get("close",0));mtf=tech.get("mtf",{});ind=tech.get("indicators",{})
    text=("━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • LIVE MARKET\n━━━━━━━━━━━━━━━━━━━━\n"
          f"🟢 SIGNAL • {tech.get('direction')}\n📈 Asset • {asset}\n💰 Entry • {entry:.6f}\n"
          f"⏱️ Expiry • {expiry} min\n🧠 Confidence • {int(tech.get('confidence',0))}%\n"
          f"📊 MTF • 1m {mtf.get('1m','-')} | 3m {mtf.get('3m','-')} | 5m {mtf.get('5m','-')}\n"
          f"📌 RSI • {float(ind.get('RSI') or 0):.1f} | ADX • {float(ind.get('ADX') or 0):.1f}\n"
          f"🧩 {', '.join(tech.get('reasons',[])[-5:])}\n📡 Source • Olymp Trade live market data\n"
          "🛡️ READ-ONLY / DEMO / MANUAL ONLY\n🚫 Auto-trade OFF • Martingale OFF")
    sender=getattr(mod,"telegram",None)
    ok=bool(sender and sender(text))
    if not ok:
        LOG.warning("WINDOW_SIGNAL_15_TO_19_FAILED window=%s asset=%s",slot,asset); return
    try:
        sent_windows.add(slot)
    except Exception: pass
    try:
        from outcome_engine import register
        b=tech.get("brain") or {}
        register({"status":"SIGNAL","asset":asset,"direction":tech.get("direction"),"expiry":expiry,"confidence":tech.get("confidence",0),"timestamp":float(data[-1].get("timestamp",time.time())),"entry":entry,"strategy":b.get("strategy","market_brain"),"regime":b.get("regime",""),"pattern":b.get("pattern",""),"features":b})
    except Exception:
        LOG.exception("Fallback outcome registration failed asset=%s",asset)
    LOG.info("WINDOW_SIGNAL_15_TO_19 window=%s asset=%s direction=%s expiry=%s confidence=%s",slot,asset,tech.get("direction"),expiry,tech.get("confidence"))


def _deadline_loop():
    LOG.info("15_TO_19_FALLBACK started | preferred=minute15 | hard_scan_end=minute19 | read_only=ON")
    last_minute=None
    while True:
        try:
            mod=sys.modules.get("__main__");feed=getattr(mod,"live_feed",None) if mod else None
            if not mod or not feed or not getattr(feed,"connected",False): time.sleep(2);continue
            now=time.time(); local=time.localtime(now); cycle_min=local.tm_min%20
            if cycle_min<15 or cycle_min>19: time.sleep(1);continue
            minute_key=(int(now//60),int(now//300))
            if minute_key==last_minute and cycle_min!=19: time.sleep(1);continue
            last_minute=minute_key
            allowed=set(feed.status().get("tradeable_assets") or []);best=None
            for asset in sorted(allowed):
                data=list(getattr(mod,"candles",{}).get(asset,[]))
                if len(data)<30: continue
                c=data[-1]
                try:
                    now2=time.time();r=float(c.get("received_at",0));ts=float(c.get("timestamp",0));ts=ts/1000 if ts>1e10 else ts;age=now2-r if r else now2-ts
                except Exception: continue
                if age>75: continue
                try:
                    threading.current_thread().candice_asset=asset;tech=mod.analyze(data)
                except Exception: continue
                if tech.get("decision")!="SIGNAL": continue
                conf=int(tech.get("confidence",0))
                if best is None or conf>best[0]: best=(conf,asset,data,tech)
            LOG.info("15_TO_19_SCAN minute=%02d:%02d best=%s",local.tm_min,local.tm_sec,(f"{best[1]} {best[0]}%" if best else "NONE"))
            if best: _send_fallback(mod,best[1],best[2],best[3],int(now//300))
            if cycle_min==19 and not best: LOG.warning("15_TO_19_DEADLINE_NO_VALID_SETUP | no signal forced")
            time.sleep(1)
        except Exception:
            LOG.exception("15_TO_19_LOOP_ERROR");time.sleep(2)


def _boot():
    while True:
        mod=sys.modules.get("__main__")
        if mod and _install_hooks(mod): break
        time.sleep(1)
    threading.Thread(target=_deadline_loop,name="candice-15to19-deadline",daemon=True).start()

threading.Thread(target=_boot,name="candice-verification-installer",daemon=True).start()

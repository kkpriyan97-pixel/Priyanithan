import asyncio,json,logging,os,time
from typing import Any
import httpx
from olymptrade_ws import OlympTradeClient
from olymptrade_ws.olympconfig import parameters
from brain_rules import BrainState,rank_signal_candidates
from candice_brain import analyze_asset
from ai_engine import snapshot_from_asset
from ai_router import analyze_with_fallback

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger("candice")
BRAIN=BrainState()
STATE={"status":"starting","assets":[],"prices":{},"candles":{},"analyses":{},"read_only":True,"cycle":0,"last_cycle":None,"account_id":None,"account_group":"demo","feed_source":"authenticated_websocket","last_asset_sync":None,"last_tick":None}
CLIENT=None
LOCK=asyncio.Lock()
LIVE_BARS={}

def pair_name(x):
    return str(x.get("pair") or x.get("p") or x.get("symbol") or x.get("instrument") or x.get("id") or "")

def display_name(x):
    for k in ("title","name","display_name","displayName"):
        if isinstance(x.get(k),str) and x[k].strip():return x[k].strip()
    return ""

def event_records(client,event_id):
    out=[]
    try:cached=client.get_cached_events(event_id)
    except Exception:cached=[]
    for m in cached or []:
        d=m.get("d") if isinstance(m,dict) else None
        if isinstance(d,list):out.extend(x for x in d if isinstance(x,dict))
    return out

def build_account_assets(raw):
    """Normalize the authenticated account-scoped asset feed only."""
    out=[]
    seen=set()
    for x in raw or []:
        if not isinstance(x,dict):
            continue
        p=pair_name(x)
        if not p or p in seen:
            continue
        unavailable=(x.get("disabled") is True or x.get("locked") is True or
                     x.get("locked_trading") is True or
                     any(k in x and x.get(k) is False for k in ("active","available","tradable","is_active","is_available","is_tradable")))
        status=str(x.get("status") or x.get("state") or "").strip().lower()
        if unavailable or status in {"disabled","locked","inactive","unavailable","closed","off"}:
            continue
        title=display_name(x)
        if not title:
            continue
        v=x.get("profitability")
        if not isinstance(v,(int,float)):
            v=x.get("payout",x.get("profit",0))
        try:
            v=int(v)
        except Exception:
            v=0
        seen.add(p)
        out.append({"pair":p,"display_name":title,"title":title,
                    "signal_asset_label":f"{title} ({p})","profitability":v,
                    "locked":False,"locked_trading":False,
                    "mode":"OTC" if "_OTC" in p.upper() else "REAL"})
    return out

def extract_asset_list(payload):
    if isinstance(payload,list):
        return [x for x in payload if isinstance(x,dict)]
    if not isinstance(payload,dict):
        return []
    d=payload.get("d",payload)
    if isinstance(d,list):
        return [x for x in d if isinstance(x,dict)]
    if isinstance(d,dict):
        for k in ("assets","profitability","instruments","pairs","data"):
            if isinstance(d.get(k),list):
                return [x for x in d[k] if isinstance(x,dict)]
    return []

async def sync_account_assets(client,account_id):
    """Read current account-visible assets through the authenticated WebSocket."""
    if not client or not account_id:
        return False
    try:
        response=await client.send_request(182,[{"account_id":account_id}],requires_response=True,timeout=8)
        raw=extract_asset_list(response)
        assets=build_account_assets(raw)
        if assets:
            previous={a["pair"]:a for a in STATE["assets"]}
            STATE["assets"]=assets
            STATE["last_asset_sync"]=time.time()
            STATE["account_id"]=account_id
            STATE["feed_source"]="authenticated_websocket:event_182"
            log.info("ACCOUNT_LIVE_ASSET_SCAN source=authenticated_websocket event=182 account_id=%s raw=%d visible=%d",account_id,len(raw),len(assets))
            return previous != {a["pair"]:a for a in assets}
        log.warning("ACCOUNT_LIVE_ASSET_SCAN_EMPTY account_id=%s",account_id)
    except Exception as e:
        log.warning("ACCOUNT_LIVE_ASSET_SCAN_FAILED account_id=%s %s",account_id,e)
    return False

async def on_asset_update(message):
    raw=extract_asset_list(message)
    if not raw:
        return
    incoming=build_account_assets(raw)
    if not incoming:
        return
    current={a["pair"]:a for a in STATE["assets"]}
    for a in incoming:
        current[a["pair"]]=a
    STATE["assets"]=list(current.values())
    STATE["last_asset_sync"]=time.time()
    STATE["feed_source"]="authenticated_websocket:event_183"
    log.info("ACCOUNT_ASSET_FEED_UPDATE event=183 visible=%d",len(STATE["assets"]))


async def telegram(text):
    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip();chat=os.getenv("TELEGRAM_CHAT_ID","").strip()
    if not token or not chat:
        log.warning("TELEGRAM_NOT_CONFIGURED")
        return False
    try:
        async with httpx.AsyncClient(timeout=8) as h:
            r=await h.post(f"https://api.telegram.org/bot{token}/sendMessage",json={"chat_id":chat,"text":text})
            r.raise_for_status();return True
    except Exception as e:
        log.warning("TELEGRAM_SEND_FAILED %s",e);return False

def _tick_timestamp(ts):
    try:
        v=float(ts)
        if v>20_000_000_000:
            v/=1000.0
        return v
    except Exception:
        return time.time()

def _update_live_bar(pair,price,ts):
    minute=int(ts//60)*60
    bars=LIVE_BARS.setdefault(pair,[])
    if not bars or int(float(bars[-1]["time"])//60)*60 != minute:
        bars.append({"time":minute,"open":price,"high":price,"low":price,"close":price,"volume":1})
        if len(bars)>120:
            del bars[:-120]
    else:
        b=bars[-1]
        b["high"]=max(float(b["high"]),price)
        b["low"]=min(float(b["low"]),price)
        b["close"]=price
        b["volume"]=int(b.get("volume",0))+1
    return bars

async def on_tick(message):
    for t in message.get("d",[]) or []:
        if not isinstance(t,dict):
            continue
        p=str(t.get("p") or t.get("pair") or "")
        q=t.get("q");ts=_tick_timestamp(t.get("t"))
        if p and q is not None:
            try:
                price=float(q)
                STATE["prices"][p]=(price,ts)
                STATE["last_tick"]=time.time()
                _update_live_bar(p,price,ts)
            except Exception:
                pass


async def refresh_candles():
    """Analyze local 1-minute candles built directly from the authenticated live tick feed."""
    assets=STATE["assets"]
    for a in assets:
        p=a["pair"]
        cs=LIVE_BARS.get(p,[])
        if cs:
            STATE["candles"][p]=list(cs[-120:])
        price=STATE["prices"].get(p,(None,None))[0]
        an=analyze_asset(a,STATE["candles"].get(p,[]),price)
        if an:
            an["profitability"]=a["profitability"]
            STATE["analyses"][p]=an
        else:
            STATE["analyses"].pop(p,None)
    log.info("LIVE_ANALYSIS_REFRESH source=local_1m_bars assets=%d qualified=%d ticks=%d",len(assets),len(STATE["analyses"]),len(STATE["prices"]))


async def final_candidate():
    BRAIN.prune_expired_cooldowns()
    eligible=BRAIN.filter_candidates(STATE["assets"])
    raw=[STATE["analyses"][a["pair"]].copy() for a in eligible if a["pair"] in STATE["analyses"]]
    raw=rank_signal_candidates(raw)
    if not raw:return None
    # AI reviews the strongest technical candidates; fallback provider is automatic.
    reviewed=[]
    for x in raw[:8]:
        cs=STATE["candles"].get(x["pair"],[])
        price=STATE["prices"].get(x["pair"],(x.get("price"),None))[0]
        snap=snapshot_from_asset(next(a for a in eligible if a["pair"]==x["pair"]),cs,price,time.time())
        try:
            d=await analyze_with_fallback(snap)
            if d and int(d.get("confidence",0))>=90:
                x.update({"confidence":int(d["confidence"]),"reason":d.get("reason") or x["reason"],"ai_provider":d.get("provider")});reviewed.append(x)
        except Exception as e:log.warning("AI_REVIEW_FAILED pair=%s %s",x["pair"],e)
    return rank_signal_candidates(reviewed)[0] if reviewed else None

async def result_watch(key):
    """Finalize a signal reliably after expiry and never silently drop a result."""
    s=BRAIN.active_signals.get(key)
    if not s:
        log.warning("RESULT_WATCH_MISSING key=%s",key)
        return
    wait=max(0,s.expiry_minutes*60-(time.time()-s.entry_ts))
    if wait: await asyncio.sleep(wait)

    # Refresh candles and retry. The old code checked one in-memory tick once;
    # when that tick was absent at expiry it returned without sending a result.
    price=None
    verification="candle-closed"
    for attempt in range(6):
        try:
            if CLIENT:
                cs=await CLIENT.market.get_candles(s.pair,size=5,count=5)
                if cs:
                    STATE["candles"][s.pair]=cs
                    closed=cs[-2] if len(cs)>=2 else cs[-1]
                    price=closed.get("close",closed.get("c"))
                    if price is not None:
                        price=float(price)
                        break
        except Exception as e:
            log.warning("RESULT_CANDLE_READ_FAILED pair=%s attempt=%d %s",s.pair,attempt+1,e)
        tick=STATE["prices"].get(s.pair,(None,None))[0]
        if tick is not None:
            price=float(tick)
            verification="tick-fallback"
            break
        await asyncio.sleep(2)

    if price is None:
        log.error("RESULT_NOT_VERIFIED pair=%s key=%s entry=%s",s.pair,key,s.entry_price)
        return
    try:
        rec=BRAIN.finish_signal(key,price)
    except Exception:
        log.exception("RESULT_FINALIZE_FAILED pair=%s key=%s",s.pair,key)
        return

    label=f"{rec['display_name']} ({rec['pair']})"
    icon={"WIN":"🟢","LOSS":"🔴","TIE":"🟡"}[rec["result"]]
    sent=await telegram(
        f"━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • TRADE RESULT\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n📈 {label}\n\n"
        f"➡️ {rec['direction']}\n\n💰 Entry: {rec['entry_price']}\n"
        f"🏁 Exit: {rec['exit_price']}\n\n⏱️ Duration: {rec['expiry_minutes']} MIN\n"
        f"🔎 Verification: {verification}\n\n{icon} {rec['result']}\n\n"
        f"⚠️ RESULT ONLY — AUTO TRADE OFF\n━━━━━━━━━━━━━━━━━━━━"
    )
    log.info("RESULT_SENT pair=%s result=%s exit=%s verification=%s telegram=%s cooldown=%s",
             rec["pair"],rec["result"],rec["exit_price"],verification,sent,rec["result"]=="LOSS")

async def cycle_loop():
    """
    Continuous 5-minute scanning:
      - exactly 3 full ALL-account-asset scans per 5-minute cycle
      - a scan never stops because another asset has no setup
      - a signal may be sent at most once per cycle
      - no user-facing NO SIGNAL message is ever sent
    """
    while True:
        now=time.time()
        next_boundary=(int(now)//300+1)*300
        cycle_id=next_boundary//300
        BRAIN.start_cycle(int(cycle_id))
        STATE["cycle"]=int(cycle_id)
        STATE["last_cycle"]=next_boundary

        # Three full-universe scans distributed across the 5-minute window.
        # The first scan is 40s before the checkpoint, then two more scans
        # approximately 100s apart. Each scan refreshes EVERY account asset.
        scan_targets=(next_boundary-40,next_boundary+100,next_boundary+200)

        for scan_no,target_ts in enumerate(scan_targets,1):
            await asyncio.sleep(max(0,target_ts-time.time()))

            # Re-sync the authenticated account-visible universe before each
            # scan so newly visible/removed assets are reflected immediately.
            if CLIENT and STATE.get("account_id"):
                try:
                    await sync_account_assets(CLIENT,STATE["account_id"])
                except Exception as e:
                    log.warning("SCAN_ASSET_SYNC_FAILED cycle=%s scan=%d %s",cycle_id,scan_no,e)

            # This is the mandatory ALL-ASSET scan. It must run even when a
            # previous scan produced no candidate or already sent a signal.
            await refresh_candles()
            log.info(
                "FULL_ASSET_SCAN cycle=%s scan=%d/3 assets=%d qualified=%d",
                cycle_id,scan_no,len(STATE["assets"]),len(STATE["analyses"])
            )

            # Try to find a signal after every full scan. If none qualifies,
            # remain silent and continue scanning; NEVER send "NO SIGNAL".
            if not BRAIN.cycle_signal_sent:
                candidate=await final_candidate()
                if candidate:
                    p=candidate["pair"]
                    entry=STATE["prices"].get(p,(None,None))[0]
                    if entry is not None and BRAIN.can_send_cycle_signal():
                        ts=time.time()
                        s=BRAIN.mark_signal_sent(
                            pair=p,display_name=candidate["display_name"],
                            direction=candidate["direction"],
                            expiry_minutes=candidate["expiry_minutes"],
                            entry_price=entry,entry_ts=ts,
                            entry_candle_ts=candidate["entry_candle_ts"],
                            strategy=candidate["strategy"],
                            reason=candidate["reason"],
                            confidence=candidate["confidence"]
                        )
                        key=f"{s.cycle_id}:{s.pair}:{s.entry_ts}"
                        target_dt=time.strftime("%H:%M:%S",time.localtime(next_boundary))
                        msg=(
                            "━━━━━━━━━━━━━━━━━━━━\\n"
                            "🎯 CANDICE AI • LIVE MARKET\\n"
                            "━━━━━━━━━━━━━━━━━━━━\\n\\n"
                            f"📊 ASSET: {s.display_name} ({s.pair})\\n"
                            f"➡️ DIRECTION: {s.direction}\\n\\n"
                            f"🕒 SIGNAL: {time.strftime('%H:%M:%S',time.localtime(ts))} UAE\\n"
                            f"🎯 TARGET: {target_dt} UAE\\n"
                            "⏳ SIGNAL COUNTDOWN: 00:40\\n\\n"
                            f"⏱️ EXPIRY: {s.expiry_minutes} MIN\\n"
                            f"💰 ENTRY: {s.entry_price}\\n\\n"
                            f"📈 15M TREND: {s.trend_15m}\\n"
                            f"🕯️ 1M STRUCTURE: {s.structure_1m}\\n"
                            f"🧠 STRATEGY: {s.strategy}\\n"
                            f"🎯 CONFIDENCE: {s.confidence}%\\n"
                            "🟢 ACCOUNT: DEMO\\n\\n"
                            f"🧠 {s.reason}\\n"
                            "━━━━━━━━━━━━━━━━━━━━"
                        )
                        await telegram(msg)
                        asyncio.create_task(result_watch(key))
                        log.info(
                            "FINAL_SIGNAL cycle=%s scan=%d pair=%s direction=%s confidence=%s",
                            cycle_id,scan_no,p,s.direction,s.confidence
                        )
                else:
                    log.info(
                        "SCAN_NO_QUALIFIED_SETUP cycle=%s scan=%d/3 assets=%d; continuing",
                        cycle_id,scan_no,len(STATE["assets"])
                    )
            else:
                log.info(
                    "SCAN_CONTINUES_AFTER_SIGNAL cycle=%s scan=%d/3 assets=%d",
                    cycle_id,scan_no,len(STATE["assets"])
                )

        # The loop immediately creates the next 5-minute cycle and repeats.
        # There is deliberately no NO_SIGNAL Telegram output.

async def market_worker():
    global CLIENT
    while True:
        token=os.getenv("OLYMPTRADE_ACCESS_TOKEN","").strip()
        if not token:
            STATE["status"]="waiting_for_token"
            await asyncio.sleep(30)
            continue
        client=OlympTradeClient(access_token=token,log_raw_messages=False)
        CLIENT=client
        client.register_callback(parameters.E_TICK_UPDATE,on_tick)
        client.register_callback(parameters.E_ASSET_PROFITABILITY_UPDATE,on_asset_update)
        try:
            STATE["status"]="connecting"
            await client.start()
            STATE["status"]="connected"
            await asyncio.sleep(4)

            # Read only the DEMO account identity from the authenticated session.
            for m in client.get_cached_events(55):
                d=m.get("d") if isinstance(m,dict) else None
                if isinstance(d,list):
                    for a in d:
                        if isinstance(a,dict) and a.get("group")=="demo":
                            client.account_id=a.get("account_id")
                            client.account_group="demo"
                            break
                if client.account_id:
                    break

            STATE["account_id"]=client.account_id
            STATE["account_group"]="demo"
            if not client.account_id:
                raise RuntimeError("DEMO_ACCOUNT_ID_NOT_FOUND_FROM_AUTHENTICATED_SESSION")

            changed=await sync_account_assets(client,client.account_id)
            if not STATE["assets"]:
                raise RuntimeError("AUTHENTICATED_ACCOUNT_RETURNED_NO_VISIBLE_ASSETS")

            STATE["status"]="live_account_read_only"
            log.info("ACCOUNT_ASSETS_READY count=%d changed=%s source=%s",len(STATE["assets"]),changed,STATE["feed_source"])

            subscribed=set()
            last_rescan=0.0
            while True:
                now=time.time()

                # Re-scan the account-visible universe every minute.
                if now-last_rescan>=60:
                    changed=await sync_account_assets(client,client.account_id)
                    if changed or not subscribed:
                        for a in STATE["assets"]:
                            p=a["pair"]
                            if p in subscribed:
                                continue
                            try:
                                await client.market.subscribe_ticks(p)
                                subscribed.add(p)
                            except Exception as e:
                                log.debug("TICK_SUBSCRIBE_FAILED %s %s",p,e)
                    last_rescan=now

                await refresh_candles()
                await asyncio.sleep(5)

        except Exception as e:
            STATE["status"]="error"
            log.exception("MARKET_WORKER_ERROR %s",e)
            await asyncio.sleep(15)
        finally:
            try:
                await client.stop()
            except Exception:
                pass
            CLIENT=None


async def health(reader,writer):
    try:
        await reader.read(2048)
        body=json.dumps({"service":"CANDICE-AI","status":STATE["status"],"read_only":True,"asset_count":len(STATE["assets"]),"qualified":len(STATE["analyses"]),"cycle":STATE["cycle"],"active_results":len(BRAIN.active_signals),"account_id":STATE["account_id"],"account_group":STATE["account_group"],"feed_source":STATE["feed_source"],"last_asset_sync":STATE["last_asset_sync"],"last_tick":STATE["last_tick"]}).encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n"+body);await writer.drain()
    finally:writer.close()

async def main():
    port=int(os.getenv("PORT","10000"));server=await asyncio.start_server(health,"0.0.0.0",port)
    await asyncio.gather(market_worker(),cycle_loop(),server.serve_forever())
if __name__=="__main__":asyncio.run(main())

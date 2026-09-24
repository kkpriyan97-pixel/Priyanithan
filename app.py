import asyncio,json,logging,os,time
from typing import Any
import httpx
from olymptrade_ws import OlympTradeClient
from olymptrade_ws.olympconfig import parameters
from brain_rules import BrainState,rank_signal_candidates,CYCLE_SECONDS
from candice_brain import analyze_asset
from ai_engine import snapshot_from_asset
from ai_router import analyze_with_fallback
from self_learning import self_learning_loop, learning_status, record_market_snapshot

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger("candice")
BRAIN=BrainState()
STATE={"status":"starting","assets":[],"prices":{},"candles":{},"analyses":{},"read_only":True,"cycle":0,"last_cycle":None,"account_id":None,"account_group":"demo","feed_source":"authenticated_websocket","last_asset_sync":None,"last_tick":None,"auto_trade_demo_enabled":False}
CLIENT=None
AUTO_TRADE_DEMO=os.getenv("AUTO_TRADE_DEMO","true").strip().lower() in {"1","true","yes","on"}
try:
    DEMO_TRADE_AMOUNT=max(0.01,float(os.getenv("DEMO_TRADE_AMOUNT","1")))
except (TypeError,ValueError):
    DEMO_TRADE_AMOUNT=1.0
AUTO_TRADE_STATUS={}
LOCK=asyncio.Lock()
LIVE_BARS={}
CANDLE_FETCH_SEMAPHORE=asyncio.Semaphore(16)
CANDLE_REFRESH_SECONDS=45
TICK_STALE_SECONDS=75
TIMING_TOLERANCE_SECONDS=1.5
HISTORY_LAST_FETCH={}
HISTORY_CURSOR=0
HISTORY_SEED_SEEN=set()
CYCLE_CANDIDATES={}
CYCLE_REVIEW_TASKS={}
SCAN_SYNC_TIMEOUT=6.0
FINAL_CACHE_MAX_AGE=75.0
AI_REVIEW_TIMEOUT=24.0
MAX_REVIEW_TASKS_PER_CYCLE=12

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


async def telegram(text,attempts=3):
    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    chat=os.getenv("TELEGRAM_CHAT_ID","").strip()
    if not token or not chat:
        log.warning("TELEGRAM_NOT_CONFIGURED")
        return False

    for attempt in range(1,attempts+1):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0,connect=2.0)) as h:
                r=await h.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id":chat,"text":text},
                )
                r.raise_for_status()
                log.info("TELEGRAM_DELIVERED attempt=%d",attempt)
                return True
        except Exception as e:
            log.warning(
                "TELEGRAM_SEND_FAILED attempt=%d/%d error=%s",
                attempt,attempts,e
            )
            if attempt<attempts:
                await asyncio.sleep(0.5*attempt)
    return False


async def place_demo_order(s,key):
    """Place the approved signal on the authenticated DEMO account at entry time."""
    if not AUTO_TRADE_DEMO:
        AUTO_TRADE_STATUS[key]={"status":"OFF"}
        return False
    client=CLIENT
    if not client or not client.connection.is_connected:
        AUTO_TRADE_STATUS[key]={"status":"FAILED","reason":"client_not_connected"}
        log.error("DEMO_AUTO_TRADE_FAILED key=%s reason=client_not_connected",key)
        return False
    if str(getattr(client,"account_group",STATE.get("account_group",""))).lower()!="demo":
        AUTO_TRADE_STATUS[key]={"status":"BLOCKED","reason":"non_demo_account"}
        log.error("DEMO_AUTO_TRADE_BLOCKED key=%s group=%s",key,getattr(client,"account_group",None))
        return False
    account_id=getattr(client,"account_id",None) or STATE.get("account_id")
    if account_id is None:
        AUTO_TRADE_STATUS[key]={"status":"FAILED","reason":"demo_account_id_missing"}
        log.error("DEMO_AUTO_TRADE_FAILED key=%s reason=demo_account_id_missing",key)
        return False
    lag=time.time()-float(s.scheduled_entry_ts)
    if lag>5.0:
        AUTO_TRADE_STATUS[key]={"status":"SKIPPED_LATE","lag":round(lag,3)}
        log.warning("DEMO_AUTO_TRADE_SKIPPED key=%s lag=%.3f",key,lag)
        return False
    direction=str(s.direction).lower()
    if direction not in {"up","down"}:
        AUTO_TRADE_STATUS[key]={"status":"FAILED","reason":"invalid_direction"}
        return False
    try:
        result=await asyncio.wait_for(
            client.trade.place_order(
                pair=s.pair,
                amount=DEMO_TRADE_AMOUNT,
                direction=direction,
                duration=int(s.expiry_minutes)*60,
                account_id=int(account_id),
                group="demo",
                category="digital",
            ),
            timeout=8.0,
        )
        if result and result.get("id"):
            AUTO_TRADE_STATUS[key]={"status":"EXECUTED","order_id":result.get("id")}
            log.info("DEMO_AUTO_TRADE_EXECUTED key=%s pair=%s direction=%s amount=%s duration=%ss account_id=%s order_id=%s",
                     key,s.pair,direction,DEMO_TRADE_AMOUNT,int(s.expiry_minutes)*60,account_id,result.get("id"))
            return True
        AUTO_TRADE_STATUS[key]={"status":"FAILED","reason":"broker_rejected","response":result}
        log.error("DEMO_AUTO_TRADE_REJECTED key=%s pair=%s response=%s",key,s.pair,result)
        return False
    except Exception as e:
        AUTO_TRADE_STATUS[key]={"status":"FAILED","reason":str(e)}
        log.exception("DEMO_AUTO_TRADE_EXCEPTION key=%s pair=%s error=%s",key,s.pair,e)
        return False


async def auto_trade_at_entry(key):
    """Wake at the scheduled boundary and submit the approved signal without verifier delay."""
    s=BRAIN.active_signals.get(key)
    if not s:
        AUTO_TRADE_STATUS[key]={"status":"FAILED","reason":"active_signal_missing"}
        return
    await asyncio.sleep(max(0,float(s.scheduled_entry_ts)-time.time()))
    await place_demo_order(s,key)


def reference_price_for(pair,candidate,now=None):
    """Return the freshest non-empty reference without requiring a live tick subscription."""
    now=time.time() if now is None else float(now)
    tick=STATE["prices"].get(pair)
    if tick and tick[0] is not None:
        try:
            age=now-float(tick[1])
            if age<=120:
                return float(tick[0]),"live-tick",age
        except Exception:
            pass

    bars=STATE["candles"].get(pair,[])
    if bars:
        try:
            normalized=[]
            for raw in bars:
                if not isinstance(raw,dict):
                    continue
                ts=float(raw.get("time",raw.get("t")))
                if ts>20_000_000_000:
                    ts/=1000
                close=raw.get("close",raw.get("c"))
                if close is not None:
                    normalized.append((int(ts//60)*60,float(close)))
            closed=[x for x in normalized if x[0]+60<=now-1]
            if closed:
                ts,price=max(closed,key=lambda x:x[0])
                return price,"closed-candle",max(0,now-(ts+60))
        except Exception:
            pass

    fallback=candidate.get("price")
    if fallback is not None:
        return float(fallback),"candidate-cache",0
    return None,"none",None

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


async def _fetch_history(pair):
    """Fetch a fresh authenticated 1-minute candle window for one account asset."""
    if not CLIENT:
        return None
    async with CANDLE_FETCH_SEMAPHORE:
        try:
            return await asyncio.wait_for(
                CLIENT.market.get_candles(pair,size=60,count=120,solid=False),
                timeout=5.0,
            )
        except Exception as e:
            log.warning("CANDLE_REFRESH_FAILED pair=%s %s",pair,e)
            return None


async def refresh_history_batch(limit=24,force=False):
    """Refresh a bounded rotating batch so one scan can never block the 3-minute scheduler."""
    global HISTORY_CURSOR
    assets=list(STATE["assets"])
    if not assets:
        return 0

    now=time.time()
    selected=[]
    n=len(assets)
    for offset in range(n):
        idx=(HISTORY_CURSOR+offset)%n
        a=assets[idx]
        p=a["pair"]
        local=LIVE_BARS.get(p,[])
        tick_ts=STATE["prices"].get(p,(None,None))[1]
        tick_fresh=tick_ts is not None and now-float(tick_ts)<=TICK_STALE_SECONDS
        last_fetch=float(HISTORY_LAST_FETCH.get(p,0) or 0)
        missing=len(local)<60 and len(STATE["candles"].get(p,[]))<60
        stale=(not tick_fresh and now-last_fetch>=CANDLE_REFRESH_SECONDS)
        if force or missing or stale:
            selected.append(a)
            if len(selected)>=limit:
                break
    if not selected:
        HISTORY_CURSOR=(HISTORY_CURSOR+limit)%n
        return 0

    # Always advance so a full-account seed/rotation cannot get stuck on one slice.
    HISTORY_CURSOR=(HISTORY_CURSOR+len(selected))%n

    jobs={a["pair"]:asyncio.create_task(_fetch_history(a["pair"])) for a in selected}
    results=await asyncio.gather(*jobs.values(),return_exceptions=True)
    refreshed=0
    for p,cs in zip(jobs,results):
        if isinstance(cs,list) and cs:
            STATE["candles"][p]=list(cs[-120:])
            HISTORY_LAST_FETCH[p]=time.time()
            HISTORY_SEED_SEEN.add(p)
            refreshed+=1
    if refreshed:
        log.info(
            "HISTORY_BATCH_REFRESH refreshed=%d requested=%d cursor=%d total_assets=%d",
            refreshed,len(selected),HISTORY_CURSOR,len(assets)
        )
    return refreshed


async def refresh_candles():
    """Analyze the entire current account universe without doing network I/O."""
    assets=list(STATE["assets"])
    now=time.time()
    qualified=0
    stale=0

    for a in assets:
        p=a["pair"]
        local=list(LIVE_BARS.get(p,[])[-120:])
        base=STATE["candles"].get(p,[])
        if local:
            merged={str(x.get("time",x.get("t"))):x for x in base if isinstance(x,dict)}
            for bar in local:
                merged[str(bar["time"])]=bar
            base=list(merged.values())
            base.sort(key=lambda x:float(x.get("time",x.get("t",0)) or 0))
            base=base[-120:]
            STATE["candles"][p]=base

        price,price_ts=STATE["prices"].get(p,(None,None))
        if price is None and base:
            last=base[-1]
            price=last.get("close",last.get("c"))
            try:
                price_ts=float(last.get("time",last.get("t",now)))+60
            except Exception:
                price_ts=now

        try:
            record_market_snapshot(p, base, price, now)
        except Exception as e:
            log.debug("LEARNING_MARKET_SNAPSHOT_FAILED pair=%s error=%s",p,e)

        an=analyze_asset(a,base,price,now=now)
        if an:
            an["profitability"]=a["profitability"]
            an["price_ts"]=price_ts
            STATE["analyses"][p]=an
            qualified+=1
        else:
            STATE["analyses"].pop(p,None)

        closed_ts=an.get("closed_1m_ts") if an else None
        if closed_ts is None or now-(float(closed_ts)+60)>90:
            stale+=1

    log.info(
        "LIVE_ANALYSIS_REFRESH source=current_account_state assets=%d qualified=%d "
        "ticks=%d stale=%d closed_only=1",
        len(assets),qualified,len(STATE["prices"]),stale
    )


async def _review_candidate(cycle_id,scan_no,x,eligible):
    cs=STATE["candles"].get(x["pair"],[])
    price=STATE["prices"].get(x["pair"],(x.get("price"),None))[0]
    asset=next(a for a in eligible if a["pair"]==x["pair"])
    closed_price=(cs[-1].get("close",cs[-1].get("c")) if cs else price)
    snap=snapshot_from_asset(asset,cs,closed_price,time.time(),technical_features=x.get("indicator_features",{}))
    try:
        try:
            d=await asyncio.wait_for(analyze_with_fallback(snap),timeout=AI_REVIEW_TIMEOUT)
        except Exception as exc:
            # The deterministic two-indicator brain remains live when AI is unavailable.
            # AI is a verifier/conflict guard, never the source of direction.
            log.warning(
                "AI_REVIEW_BYPASS cycle=%s scan=%s pair=%s reason=%s",
                cycle_id,scan_no,x["pair"],exc
            )
            x["ai_review_status"]="UNAVAILABLE_BYPASS"
            x["ai_provider"]=None
            return x

        ai_direction=str(d.get("direction","")).upper() if d else ""
        brain_direction=str(x.get("direction","")).upper()

        if ai_direction and ai_direction!=brain_direction:
            log.info(
                "AI_DIRECTION_MISMATCH cycle=%s scan=%s pair=%s brain=%s ai=%s; rejecting",
                cycle_id,scan_no,x["pair"],brain_direction,ai_direction
            )
            return None

        ai_conf=int(d.get("confidence",0)) if d else 0
        if ai_direction==brain_direction and ai_conf<70:
            log.info(
                "AI_LOW_CONFIDENCE cycle=%s scan=%s pair=%s confidence=%s; bypassing soft verifier",
                cycle_id,scan_no,x["pair"],ai_conf
            )

        x["ai_review_candle_ts"]=x.get("entry_candle_ts")
        x.update({
            "confidence":min(99,max(int(x.get("confidence",90)),ai_conf)) if d else int(x.get("confidence",90)),
            "reason":d.get("reason") or x["reason"],
            "ai_provider":d.get("provider"),
            "ai_direction":ai_direction,
            "decision_candle_closed":True,
            "reviewed_at":time.time(),
            "review_cycle":cycle_id,
            "review_scan":scan_no,
        })
        return x
    except Exception as e:
        log.warning(
            "AI_REVIEW_FAILED cycle=%s scan=%d pair=%s error=%s",
            cycle_id,scan_no,x["pair"],e
        )
        return None


async def prepare_cycle_candidates(cycle_id,scan_no):
    """Pre-compute AI-verified candidates before the T-30 final window."""
    try:
        eligible=BRAIN.filter_candidates(list(STATE["assets"]))
        raw=[
            STATE["analyses"][a["pair"]].copy()
            for a in eligible
            if a["pair"] in STATE["analyses"]
        ]
        raw=rank_signal_candidates(raw)[:2]
        if not raw:
            log.info(
                "AI_REVIEW_CACHE_EMPTY cycle=%s scan=%d reason=no_brain_candidates",
                cycle_id,scan_no
            )
            return

        reviewed=await asyncio.gather(
            *(_review_candidate(cycle_id,scan_no,x,eligible) for x in raw),
            return_exceptions=True,
        )
        accepted=[x for x in reviewed if isinstance(x,dict)]
        cache=CYCLE_CANDIDATES.setdefault(cycle_id,[])
        cache.extend(accepted)
        # Keep only the newest candidate per pair/candle.
        unique={}
        for x in cache:
            unique[(x["pair"],str(x["entry_candle_ts"]))]=x
        CYCLE_CANDIDATES[cycle_id]=list(unique.values())[-12:]

        log.info(
            "AI_REVIEW_CACHE cycle=%s scan=%d brain=%d accepted=%d cached=%d",
            cycle_id,scan_no,len(raw),len(accepted),len(CYCLE_CANDIDATES[cycle_id])
        )
    except Exception as e:
        log.exception(
            "AI_REVIEW_CACHE_FAILED cycle=%s scan=%d error=%s",
            cycle_id,scan_no,e
        )


async def _cleanup_review_tasks(keep_cycle=None):
    """Cancel completed/obsolete AI review tasks so they cannot leak across cycles."""
    pending=[]
    for cid,tasks in list(CYCLE_REVIEW_TASKS.items()):
        if keep_cycle is not None and cid==keep_cycle:
            continue
        for task in tasks:
            if not task.done():
                task.cancel()
                pending.append(task)
        CYCLE_REVIEW_TASKS.pop(cid,None)
    if pending:
        await asyncio.gather(*pending,return_exceptions=True)

async def _finish_review_tasks(cycle_id):
    """Release the current cycle's remaining review tasks after final selection."""
    tasks=CYCLE_REVIEW_TASKS.pop(cycle_id,[])
    pending=[task for task in tasks if not task.done()]
    if pending:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending,return_exceptions=True)

def select_cached_candidate(cycle_id):
    """Select a still-fresh AI-verified candidate without making a new network call."""
    now=time.time()
    candidates=[]
    current_by_pair={a["pair"]:a for a in STATE["assets"]}

    for x in CYCLE_CANDIDATES.get(cycle_id,[]):
        reviewed_at=float(x.get("reviewed_at",0) or 0)
        if now-reviewed_at>FINAL_CACHE_MAX_AGE:
            continue
        pair=x["pair"]
        current=STATE["analyses"].get(pair)
        if not current:
            continue
        # The technical brain must be current, while AI approval may come from the
        # most recent pre-check. Do not require the AI-reviewed candle to remain
        # identical because a new closed 1m candle normally appears before T-30.
        if str(current.get("direction","")).upper()!=str(x.get("direction","")).upper():
            log.info(
                "FINAL_CACHE_STALE_DIRECTION cycle=%s pair=%s cached=%s current=%s; rejecting",
                cycle_id,pair,x.get("direction"),current.get("direction")
            )
            continue

        current_closed_ts=current.get("closed_1m_ts",current.get("entry_candle_ts"))
        try:
            current_closed_age=now-(float(current_closed_ts)+60)
        except Exception:
            current_closed_age=float("inf")
        if current_closed_age>90:
            log.info(
                "FINAL_CACHE_STALE_BRAIN cycle=%s pair=%s closed_ts=%s age=%.1f; rejecting",
                cycle_id,pair,current_closed_ts,current_closed_age
            )
            continue

        asset=current_by_pair.get(pair)
        if not asset or asset.get("locked") or asset.get("locked_trading"):
            continue

        merged=current.copy()
        merged.update(x)
        # Preserve the current technical-brain candle as the actual decision candle.
        merged["entry_candle_ts"]=current.get("entry_candle_ts")
        merged["closed_1m_ts"]=current.get("closed_1m_ts")
        merged["closed_15m_ts"]=current.get("closed_15m_ts")
        merged["decision_candle_closed"]=True
        merged["brain_current_at_final"]=time.time()
        candidates.append(merged)

    ranked=rank_signal_candidates(candidates)
    if ranked:
        return ranked[0]

    log.info(
        "FINAL_CACHE_MISS cycle=%s cached=%d max_age=%ss",
        cycle_id,len(CYCLE_CANDIDATES.get(cycle_id,[])),int(FINAL_CACHE_MAX_AGE)
    )
    return None

async def result_watch(key):
    """Lock the real entry at the scheduled boundary, then verify its exact expiry candle."""
    s=BRAIN.active_signals.get(key)
    if not s:
        log.warning("RESULT_WATCH_MISSING key=%s",key)
        return

    scheduled_entry_ts=float(s.scheduled_entry_ts)
    await asyncio.sleep(max(0,scheduled_entry_ts-time.time()))

    # Capture the actual price at the scheduled entry boundary.
    actual_entry=None
    for attempt in range(10):
        tick=STATE["prices"].get(s.pair)
        if tick and tick[0] is not None and tick[1] is not None:
            tick_price=float(tick[0])
            tick_ts=float(tick[1])
            if scheduled_entry_ts-0.5 <= tick_ts <= scheduled_entry_ts+5.0:
                actual_entry=tick_price
                log.info(
                    "ENTRY_LOCKED pair=%s scheduled_entry=%d actual_entry=%s tick_ts=%.3f",
                    s.pair,int(scheduled_entry_ts),actual_entry,tick_ts
                )
                break
        await asyncio.sleep(0.5)

    # If the exact boundary tick was missed, use the scheduled candle open only.
    if actual_entry is None and CLIENT:
        try:
            cs=await CLIENT.market.get_candles(
                s.pair,size=60,count=5,solid=False,end_time=int(scheduled_entry_ts)+2
            )
            if cs:
                for raw in cs:
                    if not isinstance(raw,dict):
                        continue
                    try:
                        ts=float(raw.get("time",raw.get("t")))
                        if ts>20_000_000_000:
                            ts/=1000
                        ts=int(ts//60)*60
                    except Exception:
                        continue
                    if ts==int(scheduled_entry_ts):
                        op=raw.get("open",raw.get("o"))
                        if op is not None:
                            actual_entry=float(op)
                            log.info(
                                "ENTRY_LOCKED_FALLBACK pair=%s scheduled_entry=%d actual_entry=%s source=candle-open",
                                s.pair,int(scheduled_entry_ts),actual_entry
                            )
                        break
        except Exception as e:
            log.warning("ENTRY_CANDLE_OPEN_FAILED pair=%s %s",s.pair,e)

    if actual_entry is None:
        log.error(
            "RESULT_NOT_VERIFIED pair=%s key=%s reason=actual-entry-price-missing scheduled_entry=%d",
            s.pair,key,int(scheduled_entry_ts)
        )
        return

    s.entry_price=actual_entry

    expiry_close_ts=scheduled_entry_ts+s.expiry_minutes*60
    target_candle_start=scheduled_entry_ts+(s.expiry_minutes-1)*60
    await asyncio.sleep(max(0,expiry_close_ts-time.time())+1.0)

    exit_price=None
    for attempt in range(15):
        try:
            if CLIENT:
                cs=await CLIENT.market.get_candles(s.pair,size=60,count=5,solid=True)
                if cs:
                    for raw in cs:
                        if not isinstance(raw,dict):
                            continue
                        try:
                            ts=float(raw.get("time",raw.get("t")))
                            if ts>20_000_000_000:
                                ts/=1000
                            ts=int(ts//60)*60
                        except Exception:
                            continue
                        if ts==int(target_candle_start):
                            close=raw.get("close",raw.get("c"))
                            if close is not None:
                                exit_price=float(close)
                                break
                    if exit_price is not None:
                        break
        except Exception as e:
            log.warning(
                "RESULT_CANDLE_READ_FAILED pair=%s attempt=%d target=%d %s",
                s.pair,attempt+1,int(target_candle_start),e
            )
        await asyncio.sleep(2)

    if exit_price is None:
        log.error(
            "RESULT_NOT_VERIFIED pair=%s key=%s reason=exact-expiry-candle-missing "
            "scheduled_entry=%d expiry_close=%d verification_candle=%d",
            s.pair,key,int(scheduled_entry_ts),int(expiry_close_ts),int(target_candle_start)
        )
        return

    try:
        rec=BRAIN.finish_signal(key,exit_price)
    except Exception:
        log.exception("RESULT_FINALIZE_FAILED pair=%s key=%s",s.pair,key)
        return

    label=f"{rec['display_name']} ({rec['pair']})"
    icon={"WIN":"🟢","LOSS":"🔴","TIE":"🟡"}[rec["result"]]
    trade_state=AUTO_TRADE_STATUS.get(key,{}).get("status","NOT_EXECUTED")
    trade_icon="🤖 DEMO AUTO-TRADE: EXECUTED" if trade_state=="EXECUTED" else f"🤖 DEMO AUTO-TRADE: {trade_state}"
    sent=await telegram(
        f"━━━━━━━━━━━━━━━━━━━━\n🎯 CANDICE AI • TRADE RESULT\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n📈 {label}\n\n"
        f"➡️ {rec['direction']}\n\n💰 Entry: {rec['entry_price']}\n"
        f"🏁 Exit: {rec['exit_price']}\n\n⏱️ Duration: {rec['expiry_minutes']} MIN\n"
        f"🔎 Verification: candle-closed\n\n{icon} {rec['result']}\n\n"
        f"{trade_icon}\n━━━━━━━━━━━━━━━━━━━━"
    )
    log.info(
        "RESULT_SENT pair=%s result=%s entry=%s exit=%s verification=candle-closed "
        "scheduled_entry=%d verification_candle=%d telegram=%s cooldown=%s",
        rec["pair"],rec["result"],rec["entry_price"],rec["exit_price"],
        int(scheduled_entry_ts),int(target_candle_start),sent,rec["result"]=="LOSS"
    )

async def cycle_loop():
    """
    Deterministic 3-minute scheduler:
      T-120, T-90, T-60 = full-universe analysis snapshots
      T-30 = signal emission only; never block on candle/history/AI network work
    """
    while True:
        now=time.time()
        next_boundary=(int(now)//CYCLE_SECONDS+1)*CYCLE_SECONDS
        cycle_id=next_boundary//CYCLE_SECONDS
        BRAIN.start_cycle(int(cycle_id))
        STATE["cycle"]=int(cycle_id)
        STATE["last_cycle"]=next_boundary
        await _cleanup_review_tasks()
        for old_cycle in list(CYCLE_CANDIDATES):
            if old_cycle < cycle_id-1:
                CYCLE_CANDIDATES.pop(old_cycle,None)

        scan_targets=(next_boundary-120,next_boundary-90,next_boundary-60)

        for scan_no,target_ts in enumerate(scan_targets,1):
            await asyncio.sleep(max(0,target_ts-time.time()))
            scan_started=time.time()
            log.info(
                "CYCLE_SCAN_START cycle=%s scan=%d/3 target=%d lag=%.3f",
                cycle_id,scan_no,target_ts,scan_started-target_ts
            )

            if CLIENT and STATE.get("account_id"):
                try:
                    await asyncio.wait_for(
                        sync_account_assets(CLIENT,STATE["account_id"]),
                        timeout=SCAN_SYNC_TIMEOUT,
                    )
                except Exception as e:
                    log.warning(
                        "SCAN_ASSET_SYNC_BOUNDED cycle=%s scan=%d error=%s",
                        cycle_id,scan_no,e
                    )

            # No candle API/network wait here. The background market worker feeds state.
            try:
                await refresh_candles()
            except Exception as e:
                # One bad asset/candle must never terminate the deterministic cycle loop.
                log.exception(
                    "CYCLE_ANALYSIS_ISOLATED_FAILURE cycle=%s scan=%d error=%s",
                    cycle_id,scan_no,e
                )
            log.info(
                "FULL_ASSET_SCAN cycle=%s scan=%d/3 assets=%d qualified=%d duration=%.3f",
                cycle_id,scan_no,len(STATE["assets"]),len(STATE["analyses"]),
                time.time()-scan_started
            )

            # AI verification starts now and runs independently of the scheduler.
            task=asyncio.create_task(prepare_cycle_candidates(cycle_id,scan_no))
            tasks=CYCLE_REVIEW_TASKS.setdefault(cycle_id,[])
            tasks.append(task)
            # Hard cap bookkeeping even if a future caller adds extra scans.
            if len(tasks)>MAX_REVIEW_TASKS_PER_CYCLE:
                old=tasks.pop(0)
                if not old.done():
                    old.cancel()

        final_target=next_boundary-30
        await asyncio.sleep(max(0,final_target-time.time()))

        # ZERO network waits in the final window. Use only already-completed AI reviews.\n        actual=time.time()
        lead=next_boundary-actual
        log.info(
            "FINAL_TIMING_CHECK cycle=%s target=%d actual=%.3f lead=%.3f "
            "network_wait=0",
            cycle_id,final_target,actual,lead
        )

        candidate=select_cached_candidate(cycle_id)
        if not candidate:
            await _finish_review_tasks(cycle_id)
            log.info(
                "FINAL_NO_SIGNAL cycle=%s reason=no_fresh_ai_verified_candidate",
                cycle_id
            )
            continue

        p=candidate["pair"]
        entry,entry_source,entry_age=reference_price_for(p,candidate,actual)
        log.info(
            "FINAL_REFERENCE cycle=%s pair=%s source=%s age=%s price=%s",
            cycle_id,p,entry_source,
            f"{entry_age:.1f}" if entry_age is not None else "n/a",
            entry,
        )
        if entry is None:
            log.warning(
                "FINAL_SIGNAL_NO_REFERENCE_PRICE cycle=%s pair=%s",
                cycle_id,p
            )
            continue
        if entry_source!="live-tick" and entry_age is not None and entry_age>180:
            log.warning(
                "FINAL_SIGNAL_STALE_REFERENCE cycle=%s pair=%s source=%s age=%.1f max=180",
                cycle_id,p,entry_source,entry_age
            )
            continue

        try:
            ts=actual
            s=BRAIN.mark_signal_sent(
                pair=p,
                display_name=candidate["display_name"],
                direction=candidate["direction"],
                expiry_minutes=candidate["expiry_minutes"],
                entry_price=entry,
                entry_ts=ts,
                scheduled_entry_ts=float(next_boundary),
                entry_candle_ts=candidate["entry_candle_ts"],
                strategy=candidate["strategy"],
                reason=candidate["reason"],
                confidence=candidate["confidence"],
                pattern=candidate.get("pattern",""),
                trend_15m=candidate.get("trend_15m",""),
                structure_1m=candidate.get("structure_1m",""),
                avwap=candidate.get("avwap",0),
                poc=candidate.get("poc",0),
                vah=candidate.get("vah",0),
                val=candidate.get("val",0),
                avwap_slope=candidate.get("avwap_slope",0),
                decision_candle_closed=candidate.get("decision_candle_closed",True),
            )
        except Exception as e:
            log.warning(
                "FINAL_SIGNAL_REJECTED cycle=%s pair=%s error=%s",
                cycle_id,p,e
            )
            continue

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
            "⏳ SIGNAL COUNTDOWN: 00:30\\n\\n"
            f"⏱️ EXPIRY: {s.expiry_minutes} MIN\\n"
            f"💰 REFERENCE: {entry}\\n\\n"
            f"📈 15M TREND: {s.trend_15m}\\n"
            f"🕯️ 1M STRUCTURE: {s.structure_1m}\\n"
            f"🧠 STRATEGY: {s.strategy}\\n"
            f"🎯 CONFIDENCE: {s.confidence}%\\n"
            "🟢 ACCOUNT: DEMO\\n\\n"
            f"🧠 {s.reason}\\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        sent=await telegram(msg,attempts=3)
        log.info(
            "FINAL_SIGNAL cycle=%s pair=%s direction=%s confidence=%s "
            "entry_candle=%s lead=%.3f reference_source=%s reference_age=%s telegram=%s",
            cycle_id,p,s.direction,s.confidence,s.entry_candle_ts,lead,
            entry_source,
            f"{entry_age:.1f}" if entry_age is not None else "n/a",
            sent
        )
        await _finish_review_tasks(cycle_id)
        if AUTO_TRADE_DEMO:
            asyncio.create_task(auto_trade_at_entry(key))
        asyncio.create_task(result_watch(key))

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
            requested_account=os.getenv("OLYMPTRADE_DEMO_ACCOUNT_ID","").strip()
            if requested_account:
                try:
                    client.account_id=int(requested_account)
                except ValueError as exc:
                    raise RuntimeError("INVALID_OLYMPTRADE_DEMO_ACCOUNT_ID") from exc
                client.account_group="demo"
            await client.start()
            await client.initialize_session()
            STATE["status"]="connected"

            # Bind only the DEMO account exposed by the authenticated session.
            STATE["account_id"]=client.account_id
            STATE["account_group"]="demo"
            if not client.account_id:
                raise RuntimeError("DEMO_ACCOUNT_ID_NOT_FOUND_FROM_AUTHENTICATED_SESSION")
            if str(client.account_group).lower()!="demo":
                raise RuntimeError("NON_DEMO_ACCOUNT_BLOCKED")

            changed=await sync_account_assets(client,client.account_id)
            if not STATE["assets"]:
                raise RuntimeError("AUTHENTICATED_ACCOUNT_RETURNED_NO_VISIBLE_ASSETS")

            # Background rotation seeds the full account universe without blocking the cycle scheduler.
            await refresh_candles()

            STATE["auto_trade_demo_enabled"]=bool(AUTO_TRADE_DEMO and str(client.account_group).lower()=="demo")
            STATE["status"]="live_demo_ready" if STATE["auto_trade_demo_enabled"] else "live_account_read_only"
            log.info("ACCOUNT_ASSETS_READY count=%d changed=%s source=%s account_id=%s demo_auto_trade=%s amount=%s",len(STATE["assets"]),changed,STATE["feed_source"],STATE["account_id"],STATE["auto_trade_demo_enabled"],DEMO_TRADE_AMOUNT)

            subscribed=set()
            last_rescan=0.0
            last_history_refresh=0.0
            history_seed_done=False

            while True:
                now=time.time()

                if now-last_rescan>=60:
                    changed=await sync_account_assets(client,client.account_id)
                    if changed or not subscribed:
                        # Subscribe in parallel with a bounded concurrency; failures are isolated per asset.
                        sem=asyncio.Semaphore(8)

                        async def subscribe_one(a):
                            p=a["pair"]
                            if p in subscribed:
                                return
                            async with sem:
                                try:
                                    await asyncio.wait_for(
                                        client.market.subscribe_ticks(p),
                                        timeout=4.0,
                                    )
                                    subscribed.add(p)
                                except Exception as e:
                                    log.debug("TICK_SUBSCRIBE_FAILED pair=%s error=%s",p,e)

                        await asyncio.gather(
                            *(subscribe_one(a) for a in list(STATE["assets"])),
                            return_exceptions=True,
                        )
                    last_rescan=now
                    log.info(
                        "ACCOUNT_SCAN_ROTATION assets=%d subscribed=%d",
                        len(STATE["assets"]),len(subscribed)
                    )

                # Rotate candle-history refreshes in small batches; never block the 3-minute scheduler.
                if not history_seed_done:
                    await refresh_history_batch(limit=24,force=True)
                    history_seed_done=(
                        bool(STATE["assets"])
                        and len(HISTORY_SEED_SEEN) >= len(STATE["assets"])
                    )
                    if history_seed_done:
                        log.info(
                            "HISTORY_SEED_COMPLETE assets=%d",
                            len(STATE["assets"])
                        )
                    last_history_refresh=now
                elif now-last_history_refresh>=10:
                    await refresh_history_batch(limit=24,force=False)
                    last_history_refresh=now

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
        body=json.dumps({"service":"CANDICE-AI","status":STATE["status"],"read_only":not STATE.get("auto_trade_demo_enabled",False),"auto_trade_demo_enabled":STATE.get("auto_trade_demo_enabled",False),"demo_trade_amount":DEMO_TRADE_AMOUNT,"asset_count":len(STATE["assets"]),"qualified":len(STATE["analyses"]),"cycle":STATE["cycle"],"active_results":len(BRAIN.active_signals),"account_id":STATE["account_id"],"account_group":STATE["account_group"],"feed_source":STATE["feed_source"],"last_asset_sync":STATE["last_asset_sync"],"last_tick":STATE["last_tick"],"self_learning":learning_status()},ensure_ascii=False).encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n"+body);await writer.drain()
    finally:writer.close()

async def main():
    port=int(os.getenv("PORT","10000"));server=await asyncio.start_server(health,"0.0.0.0",port)
    await asyncio.gather(market_worker(),cycle_loop(),self_learning_loop(),server.serve_forever())
if __name__=="__main__":asyncio.run(main())
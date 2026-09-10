"""Final live signal engine.
Scans a broad broker universe, uses 1m trigger + 5m context, then AI confirms.
Never places broker orders.
"""
import asyncio, json, re, sys, time

EXPIRIES=(1,2,3,5,15)
AI_CANDIDATE_LIMIT=12
AI_RETRY_PER_CANDIDATE=0
SIGNAL_COOLDOWN_SECONDS=240

def _app():
    m=sys.modules.get("__main__")
    if m is not None and getattr(m,"__file__","").endswith("app.py"): return m
    return sys.modules.get("app")

def _priority(pair):
    p=str(pair).upper(); pref=("EURUSD","GBPUSD","USDJPY","AUDUSD","USDCAD","USDCHF","EURJPY","GBPJPY","EURGBP","AUDJPY","NZDUSD","NZDJPY","ASIA_X")
    try:r=pref.index(p)
    except ValueError:r=len(pref)+1
    return (r,p)

def _find_account_id(value):
    if isinstance(value,dict):
        if str(value.get("group","")).lower()=="demo" and value.get("account_id") is not None:
            try:return int(value["account_id"])
            except Exception:pass
        for v in value.values():
            x=_find_account_id(v)
            if x:return x
    elif isinstance(value,list):
        for v in value:
            x=_find_account_id(v)
            if x:return x
    return None

def _extract_pairs(value):
    out=set(); keys=("pair","symbol","instrument","name","p")
    def walk(v):
        if isinstance(v,dict):
            for k in keys:
                x=v.get(k)
                if isinstance(x,str):
                    x=x.upper().strip()
                    if x and 2<=len(x)<=30 and x not in {"UP","DOWN","USD","EUR"}: out.add(x)
            for x in v.values(): walk(x)
        elif isinstance(v,list):
            for x in v: walk(x)
    walk(value); return out

async def _refresh_universe(a):
    discovered=getattr(a,"discovered_assets",{}) or {}
    pairs=set(discovered.keys())
    client=getattr(a,"ot_client",None)
    if client is not None:
        try:
            balance=getattr(client,"current_balance",None)
            if not balance:
                balance=await client.balance.get_balance(timeout=4,poll_interval=.25)
            account_id=_find_account_id(balance)
            if account_id:
                prof=await client.market.get_profitability(account_id)
                found=_extract_pairs(prof)
                pairs.update(found)
                if found: a.log.info("LIVE PROFITABILITY UNIVERSE: account=%s assets_found=%s",account_id,len(found))
        except Exception as exc:
            a.log.info("LIVE PROFITABILITY DISCOVERY unavailable: %s",str(exc)[:180])
    aliases=getattr(a,"PAIR_ALIASES",{}) or {}
    pairs.update(str(v).upper() for v in aliases.values() if v)
    pairs.update(str(v).upper() for v in getattr(a,"MANUAL_PAIRS",[]) if v)
    pairs.update(str(v).upper() for v in getattr(a,"PAIRS",[]) if v)
    return sorted(pairs,key=_priority)

def _signal_text(result,ai):
    direction=str(ai.get("direction","")).upper(); arrow="⬆️" if direction=="UP" else "⬇️"
    expiry=int(ai.get("duration_min",5)); confidence=int(ai.get("confidence",0)); technical=int(result.get("setup_score",result.get("confidence",0)))
    patterns="\n".join("• "+str(x) for x in (result.get("patterns") or [])[:6])
    votes=f"{result.get('indicator_votes','-')}/{result.get('indicator_total','6')}"
    mtf=result.get("higher_tf_direction") or "UNKNOWN"
    return ("🔥 PRIYANITHAN AI SIGNAL 🔥\n\n"
            f"📈 {result.get('pair')}\n\n{arrow} {direction}\n\n"
            f"💰 Entry: {result.get('price','LIVE')}\n\n⏱️ Expiry: {expiry} MIN\n\n"
            f"🤖 AI Confidence: {confidence}%\n\n📊 Setup Score: {technical}%\n"
            f"🧩 Indicators: {votes} aligned\n"
            f"🕯️ 5m Trend: {mtf}\n\n{patterns}\n\n"
            f"🕐 {result.get('candle_time','LIVE')}\n\n🧠 Candice AI: APPROVED\n\n⚠️ MANUAL TRADE — AUTO TRADE OFF")

async def _install_scan():
    a=_app()
    if not a or getattr(a,"_FINAL_SIGNAL_ENGINE",False): return bool(a)
    if not getattr(a,"scan_cycle",None): return False
    async def classic_scan(application):
        if getattr(a,"_FINAL_SCAN_RUNNING",False):
            a.log.info("FINAL SCAN SKIPPED: another scan is running"); return
        a._FINAL_SCAN_RUNNING=True
        try:
            universe=await _refresh_universe(a)
            a.log.info("FINAL LIVE SCAN START: universe=%s discovered=%s",len(universe),len(getattr(a,"discovered_assets",{}) or {}))
            candidates=[]; candle_failures=0
            for pair in universe:
                try:
                    df,err=await a.get_ot_candles(pair,60,120)
                    if df is None: candle_failures+=1; continue
                    r=a.analyze_pair(pair,df)
                    if str(r.get("signal","NO SIGNAL")).upper()=="NO SIGNAL": continue
                    try:
                        hdf,herr=await a.get_ot_candles(pair,300,60)
                        if hdf is not None:
                            hr=a.analyze_pair(pair,hdf)
                            r["higher_tf_direction"]=str(hr.get("signal","NO SIGNAL")).upper()
                            r["higher_tf_score"]=int(hr.get("setup_score",hr.get("confidence",0)) or 0)
                            r["mtf_aligned"]=r["higher_tf_direction"]==r.get("signal")
                            if r["higher_tf_direction"] not in ("NO SIGNAL",r.get("signal")): continue
                            if r["mtf_aligned"]: r["setup_score"]=min(100,int(r.get("setup_score",0))+8)
                    except Exception: pass
                    candidates.append(r)
                except Exception as exc:
                    candle_failures+=1; a.log.warning("FINAL TECHNICAL SCAN FAILED %s: %s",pair,str(exc)[:160])
            candidates.sort(key=lambda x:(float(x.get("setup_score",0)),float(x.get("confidence",0))),reverse=True)
            a.log.info("FINAL TECHNICAL SUMMARY: assets=%s candidates=%s candle_failures=%s",len(universe),len(candidates),candle_failures)
            ai_attempts=ai_rejects=ai_failures=0; rejection_reasons=[]
            now=time.time(); sent_key=getattr(a,"_FINAL_LAST_SIGNAL",None)
            for result in candidates[:AI_CANDIDATE_LIMIT]:
                ai_attempts+=1
                try:
                    prompt=a.ai_prompt(result)
                    ai,err=await asyncio.wait_for(asyncio.to_thread(a.call_ai,prompt),timeout=30)
                except Exception as exc:
                    ai_failures+=1; rejection_reasons.append(f"{result.get('pair')}: AI error"); continue
                if not ai or err:
                    ai_failures+=1; rejection_reasons.append(f"{result.get('pair')}: {err or 'no decision'}"); continue
                decision=str(ai.get("decision","")).upper(); direction=str(ai.get("direction","")).upper()
                try: conf=int(ai.get("confidence",0)); duration=int(ai.get("duration_min",5))
                except Exception: ai_rejects+=1; continue
                technical=str(result.get("signal","")).upper()
                qualified=(decision=="APPROVE" and direction==technical and direction in ("UP","DOWN") and conf>=int(getattr(a,"AI_MIN_CONFIDENCE",72)) and duration in EXPIRIES and int(result.get("indicator_votes",0) or 0)>=3 and (result.get("mtf_aligned") is not False))
                key=(str(result.get("pair")),direction,duration)
                if qualified and sent_key and sent_key.get("key")==key and now-float(sent_key.get("time",0))<SIGNAL_COOLDOWN_SECONDS:
                    qualified=False; rejection_reasons.append(f"{result.get('pair')}: duplicate cooldown")
                if qualified:
                    text=_signal_text(result,ai)
                    if await a.send_to_recipients(application.bot,text):
                        a._FINAL_LAST_SIGNAL={"key":key,"time":time.time()}
                        a.log.info("FINAL QUALIFIED SIGNAL SENT: %s %s %sM AI=%s",result.get("pair"),direction,duration,conf)
                        return
                else:
                    ai_rejects+=1; rejection_reasons.append(f"{result.get('pair')}: {str(ai.get('reason','reject'))[:120]}")
            a.log.info("FINAL CYCLE COMPLETE: assets=%s candidates=%s ai_attempts=%s rejects=%s failures=%s",len(universe),len(candidates),ai_attempts,ai_rejects,ai_failures)
            if ai_failures and ai_failures==ai_attempts:
                msg=("⚠️ AI CONFIRMATION UNAVAILABLE\n\n" f"📊 Assets scanned: {len(universe)}\n📈 Candidates: {len(candidates)}\n🤖 AI attempts: {ai_attempts}\n\nNo signal was forced because AI confirmation was unavailable.")
            else:
                reason_lines="\n".join("• "+x for x in rejection_reasons[:4])
                msg=("🚫 NO QUALIFIED SIGNAL\n\n" f"📊 Assets scanned: {len(universe)}\n📈 Technical candidates: {len(candidates)}\n🤖 AI checks: {ai_attempts}\n❌ AI rejects: {ai_rejects}\n⚠️ AI failures: {ai_failures}\n\nNo setup passed the final confirmation gate.")
                if reason_lines: msg+="\n\n🔎 Top rejection reasons:\n"+reason_lines
                msg+="\n\n⏱️ Next opportunity scan: ~60 seconds."
            await a.send_to_recipients(application.bot,msg)
        finally:
            a._FINAL_SCAN_RUNNING=False
    a.scan_cycle=classic_scan; a._FINAL_SIGNAL_ENGINE=True
    a.log.warning("FINAL SIGNAL ENGINE ACTIVE: broad universe + 1m trigger + 5m context + AI gate")
    return True

def _boot():
    for _ in range(1800):
        try:
            if asyncio.run(_install_scan()): return
        except Exception: pass
        time.sleep(.1)
import threading
threading.Thread(target=_boot,name="final-signal-engine",daemon=True).start()

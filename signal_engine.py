"""Priyanithan dual-AI signal engine.

Cerebras + Groq independently analyze live candles/indicators/price structure.
Both must approve the same direction before a signal is delivered.
Also provides 30s status updates and a 10-signal result report.
AUTO TRADE remains OFF.
"""
import asyncio, io, json, os, re, sys, time
import requests

EXPIRIES=(1,2,3,5,10,15)

def _app():
    m=sys.modules.get("__main__")
    if m is not None and getattr(m,"__file__","").endswith("app.py"): return m
    return sys.modules.get("app")

def _parse_ai(body):
    choices=body.get("choices") or [] if isinstance(body,dict) else []
    if not choices: raise ValueError("AI response has no choices")
    msg=choices[0].get("message") or {}
    vals=[msg.get("content"),choices[0].get("text"),body.get("output_text")]
    for v in vals:
        if isinstance(v,list): v="".join(str(x.get("text") or x.get("content") or "") if isinstance(x,dict) else str(x) for x in v)
        if isinstance(v,dict) and "decision" in v: return v
        if not v: continue
        text=re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$","",str(v).strip(),flags=re.I)
        try:
            x=json.loads(text)
            if isinstance(x,dict) and "decision" in x:return x
        except Exception: pass
        for m in re.finditer(r"\{",text):
            try:
                x,_=json.JSONDecoder().raw_decode(text[m.start():])
                if isinstance(x,dict) and "decision" in x:return x
            except Exception: pass
    raise ValueError("No decision JSON")

def _snapshot(result,df):
    rows=df.tail(20); last=rows.iloc[-1]; prev=rows.iloc[-2]
    body=abs(float(last.close)-float(last.open)); rng=max(float(last.high)-float(last.low),1e-12)
    upper=float(last.high)-max(float(last.open),float(last.close)); lower=min(float(last.open),float(last.close))-float(last.low)
    pattern="DOJI" if body<=rng*.12 else "BULLISH_CLOSE" if last.close>last.open else "BEARISH_CLOSE"
    if lower>body*2 and last.close>last.open: pattern="HAMMER_BULLISH"
    if upper>body*2 and last.close<last.open: pattern="SHOOTING_STAR_BEARISH"
    high=float(rows.high.max()); low=float(rows.low.min()); price=float(last.close); span=max(high-low,1e-12)
    return {"asset":result["pair"],"price":price,"direction_hint":result.get("signal"),"technical_confidence":result.get("confidence",0),"trend":result.get("trend"),"rsi":round(float(result.get("rsi",50)),2),"adx":round(float(result.get("adx",0)),2),"candle_pattern":pattern,"previous_close":float(prev.close),"support":low,"resistance":high,"near_support":abs(price-low)/span<=.20,"near_resistance":abs(high-price)/span<=.20,"binary_fixed_time_only":True,"no_forex_math":True}

def _prompt(s):
    return ("You are a conservative binary/fixed-time market analyst. Analyze ONLY this live snapshot. Use indicators, candle pattern, trend, support/resistance and price structure. Do not use forex pip math, leverage, position sizing or invented data. Select best duration only from 1,2,3,5,10,15 minutes. Reject weak, mixed or overextended setups. Return JSON only: decision APPROVE/REJECT, direction UP/DOWN/NO SIGNAL, confidence 0-100, duration_min 1/2/3/5/10/15, reason.\nSNAPSHOT:\n"+json.dumps(s,default=str))

def _call(name,url,key,model,prompt):
    r=requests.post(url,headers={"Authorization":"Bearer "+key.strip(),"Content-Type":"application/json"},json={"model":model,"messages":[{"role":"system","content":"Return exactly one JSON object. Be conservative."},{"role":"user","content":prompt}],"temperature":0,"max_tokens":350},timeout=20)
    a=_app()
    if a:a.log.info("AI ANALYST RESPONSE: %s model=%s status=%s",name,model,r.status_code)
    if not r.ok: raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    x=_parse_ai(r.json()); d=str(x.get("decision","")).upper(); direction=str(x.get("direction","")).upper(); c=int(x.get("confidence",0)); dur=int(x.get("duration_min",5))
    if d not in ("APPROVE","REJECT") or direction not in ("UP","DOWN","NO SIGNAL") or not 0<=c<=100 or dur not in EXPIRIES: raise ValueError("invalid AI output")
    x.update(decision=d,direction=direction,confidence=c,duration_min=dur,provider=name); return x

def _confirm(s):
    a=_app(); ck=os.getenv("CEREBRAS_API_KEY"); gk=os.getenv("GROQ_API_KEY")
    if not ck or not gk:
        if a:a.log.error("DUAL AI BLOCKED: both CEREBRAS_API_KEY and GROQ_API_KEY are required")
        return None
    prompt=_prompt(s); results=[]
    for name,url,key,model in [("Cerebras","https://api.cerebras.ai/v1/chat/completions",ck,os.getenv("CEREBRAS_MODEL","gpt-oss-120b")),("Groq","https://api.groq.com/openai/v1/chat/completions",gk,os.getenv("GROQ_MODEL","openai/gpt-oss-120b"))]:
        try: results.append(_call(name,url,key,model,prompt))
        except Exception as e:
            if a:a.log.warning("AI ANALYST FAILED: %s: %s",name,e)
            return None
    x,y=results
    if x["decision"]!="APPROVE" or y["decision"]!="APPROVE" or x["direction"]!=y["direction"]: return None
    conf=min(x["confidence"],y["confidence"])
    if conf<int(os.getenv("AI_MIN_CONFIDENCE","60")): return None
    return {"decision":"APPROVE","direction":x["direction"],"confidence":conf,"duration_min":min(x["duration_min"],y["duration_min"]),"reason":"Cerebras + Groq agreed","analysts":results}

def _signal_text(result,c):
    cs=next((x["confidence"] for x in c["analysts"] if x["provider"]=="Cerebras"),0); gs=next((x["confidence"] for x in c["analysts"] if x["provider"]=="Groq"),0)
    arrow="⬆️" if c["direction"]=="UP" else "⬇️"
    return ("🔥 PRIYANITHAN AI SIGNAL 🔥\n\n" f"📈 {result['pair']}\n{arrow} {c['direction']}\n💰 Entry: {result['price']}\n" f"⏱️ Expiry: {c['duration_min']} MIN\n🤖 Dual AI Confidence: {c['confidence']}%\n" f"🧠 Cerebras: {cs}% | Groq: {gs}%\n📊 Technical: {result.get('confidence',0)}%\n" f"🕐 {time.strftime('%H:%M:%S')} UAE\n\n✅ FINAL CONFIRMATION: APPROVED\n⚠️ MANUAL TRADE ONLY — AUTO TRADE OFF")

def _parse_signal(text):
    m=re.search(r"📈\s*([^\n]+).*?(⬆️\s*UP|⬇️\s*DOWN).*?💰\s*Entry:\s*([^\n]+).*?⏱️\s*Expiry:\s*(\d+)\s*MIN",str(text),re.S)
    if not m:return None
    return m.group(1).strip(),("UP" if "UP" in m.group(2) else "DOWN"),m.group(3).strip(),int(m.group(4))

async def _status(bot,text):
    data=_parse_signal(text)
    if not data:return
    pair,direction,entry,expiry=data; a=_app(); start=time.time(); total=expiry*60
    while True:
        left=total-(time.time()-start)
        if left<=0:return
        await asyncio.sleep(min(30,max(1,left)))
        left=total-(time.time()-start)
        if left<=0:return
        current=None; ticks=getattr(a,"latest_ticks",{}) if a else {}; item=ticks.get(pair.upper()) if isinstance(ticks,dict) else None
        if isinstance(item,dict):
            for k in ("price","p","last","close","value","ask","bid"):
                try:
                    if item.get(k) is not None: current=float(item[k]); break
                except Exception:pass
        for cid in (a.recipients() if a else []):
            try: await bot.send_message(chat_id=cid,text=("⏱️ PRIYANITHAN 30s LIVE UPDATE\n\n" f"📈 {pair}\n↕️ {direction}\n💰 Entry: {entry}\n📍 Current: {current if current is not None else 'LIVE'}\n" f"⏳ Time left: {int(left)//60:02d}:{int(left)%60:02d}\n📡 Status: SIGNAL ACTIVE\n⚠️ MANUAL TRADE ONLY"))
            except Exception:pass

def _install():
    a=_app()
    if not a or getattr(a,"_DUAL_AI_ENGINE",False):return bool(a)
    original_scan=getattr(a,"scan_cycle",None)
    if original_scan is None:return False
    async def dual_scan(application):
        universe=sorted(a.discovered_assets.keys())[:a.MAX_ASSETS_PER_CYCLE] if a.AUTO_DISCOVER_ASSETS else (a.MANUAL_PAIRS[:] if a.MANUAL_PAIRS else a.PAIRS[:a.MAX_ASSETS_PER_CYCLE])
        candidates=[]
        for pair in universe:
            df,err=await a.get_ot_candles(pair,60,120)
            if df is None:continue
            try:r=a.analyze_pair(pair,df); s=_snapshot(r,df)
            except Exception:continue
            if r.get("signal")!="NO SIGNAL":candidates.append((r,s))
        candidates.sort(key=lambda x:x[0].get("confidence",0),reverse=True)
        for r,s in candidates[:2]:
            c=await asyncio.to_thread(_confirm,s)
            if not c or c["direction"]!=r.get("signal"):continue
            text=_signal_text(r,c); sent=await a.send_to_recipients(application.bot,text)
            if sent:
                try:asyncio.create_task(_status(application.bot,text),name="signal-30s-status")
                except Exception:pass
                return
        await a.send_to_recipients(application.bot,"🚫 NO DUAL-AI QUALIFIED SIGNAL\n\nCerebras + Groq did not both confirm the setup.\n⏱️ Next scan: automatic 5-minute cycle.")
    a.scan_cycle=dual_scan; a._DUAL_AI_ENGINE=True; a.log.warning("FINAL DUAL AI ENGINE ACTIVE: Cerebras + Groq")
    return True

def _boot():
    for _ in range(1800):
        try:
            if _install():return
        except Exception:
            a=_app()
            if a and hasattr(a,"log"):a.log.exception("DUAL AI ENGINE INSTALL FAILED")
        time.sleep(1)

threading=None
try:
    import threading
    threading.Thread(target=_boot,name="dual-ai-engine",daemon=True).start()
except Exception:pass

def _install_ten_report():
    for _ in range(1800):
        a=_app()
        if a:
            sender=getattr(a,"send_to_recipients",None)
            if sender and not getattr(a,"_TEN_SIGNAL_REPORT",False):
                async def wrapped(bot,text):
                    sent=await sender(bot,text)
                    if "PRIYANITHAN SIGNAL RESULT" not in str(text): return sent
                    m=re.search(r"📈\s*([^\n]+).*?↕️\s*(UP|DOWN).*?📊\s*Market Result:\s*(WIN|LOSS|DRAW|UNRESOLVED)",str(text),re.S)
                    if not m:return sent
                    h=getattr(a,"signal_history",[]); h.append({"pair":m.group(1).strip(),"direction":m.group(2),"result":m.group(3)}); a.signal_history=h[-10:]
                    if len(h)==10:
                        wins=sum(x["result"]=="WIN" for x in h); losses=sum(x["result"]=="LOSS" for x in h); draws=sum(x["result"]=="DRAW" for x in h); unresolved=sum(x["result"]=="UNRESOLVED" for x in h)
                        lines="\n".join(f"{i+1}. {x['pair']} {x['direction']} → {x['result']}" for i,x in enumerate(h))
                        report=("📊 PRIYANITHAN — 10 SIGNAL FULL REPORT\n\n" f"Signals: 10\n✅ WIN: {wins}\n❌ LOSS: {losses}\n➖ DRAW: {draws}\n⚠️ UNRESOLVED: {unresolved}\n📈 Win Rate: {wins*10:.1f}%\n\n"+lines+"\n\n🔄 10-signal cycle complete.\n⏱️ Next signal scan continues automatically every 5 minutes.\n⚠️ SIGNAL RESULT ONLY — MANUAL TRADE / AUTO TRADE OFF")
                        await sender(bot,report); a.signal_history=[]
                    return sent
                wrapped._TEN_SIGNAL_REPORT=True; a.send_to_recipients=wrapped; a.signal_history=[]; a._TEN_SIGNAL_REPORT=True; return
        time.sleep(1)

try:
    import threading as _tr
    _tr.Thread(target=_install_ten_report,name="ten-signal-report",daemon=True).start()
except Exception:pass

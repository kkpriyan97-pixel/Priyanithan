from __future__ import annotations
import asyncio, time

FETCH_TIMEOUT=30
MAX_CONCURRENCY=3
TIMEFRAMES=(1,2,3,5,10,15)


def install(app):
    """Independent 24/7 closed-1m observation loop; never places trades.
    Reviews every live asset on 1m/2m/3m/5m/10m/15m and lets Own Strategy V4
    choose the strongest qualified context. After a loss it requires stricter
    multi-timeframe agreement rather than forcing a signal."""
    if getattr(app,'_candice_own_brain_247_task',None) is not None:return
    import candice_engine as engine
    from candice_strategy_v4 import build_plan
    app._candice_brain_247_last_minute={}
    app._candice_own_brain_247_task=None

    def ts_of(v):
        try:return float(v.timestamp()) if hasattr(v,'timestamp') else float(v)
        except Exception:return None

    def plan_score(frames, tf, plan, recovery=False):
        direction=str(getattr(plan,'next_candle_direction','')).upper()
        if direction not in {'UP','DOWN'} or getattr(plan,'wait',True):return -1.0
        aligned=sum(str(v.get('direction','')).upper()==direction for v in frames.values())
        snap=frames[tf]
        adx=float(snap.get('adx14',0) or 0); strength=float(snap.get('strength',0) or 0)
        agreement=float(snap.get('indicator_agreement',0) or 0); body=float(snap.get('body_ratio',0) or 0)
        score=aligned*10+min(strength,1)*18+min(adx,40)*.45+min(agreement,5)*2.5+min(body,1)*5
        if tf in (10,15):score+=4
        if tf==2:score+=1
        if recovery:score += 3 if aligned>=4 else -8
        return score

    async def observe(asset,sem):
        async with sem:
            try:
                app.log.info('CANDICE OWN BRAIN 24/7 FETCH START pair=%s',asset)
                try:
                    df,err=await asyncio.wait_for(app.broker.candles(asset,60,1000,360),timeout=FETCH_TIMEOUT)
                except asyncio.TimeoutError:
                    app.log.warning('CANDICE OWN BRAIN 24/7 FETCH TIMEOUT pair=%s',asset);return
                n=len(df) if df is not None else 0
                app.log.info('CANDICE OWN BRAIN 24/7 FETCH DONE pair=%s rows=%s err=%s',asset,n,err or '')
                if err or df is None or n<120:
                    app.log.info('CANDICE OWN BRAIN 24/7 WAIT pair=%s reason=%s',asset,err or 'insufficient candles');return
                closed=engine.closed_1m(df)
                if len(closed)<120:
                    app.log.info('CANDICE OWN BRAIN 24/7 WAIT pair=%s reason=insufficient closed candles count=%s',asset,len(closed));return
                candle_ts=ts_of(closed.iloc[-1].get('timestamp'))
                if candle_ts is None:return
                minute=int(candle_ts//60)
                if app._candice_brain_247_last_minute.get(asset)==minute:return
                app._candice_brain_247_last_minute[asset]=minute
                app.log.info('CANDICE OWN BRAIN 24/7 CLOSED READY pair=%s candle_ts=%s',asset,candle_ts)
                frames={'1m':engine.technical_snapshot(closed)}
                for mins in (2,3,5,10,15):
                    try:
                        agg=engine.resample_ohlc(closed,mins)
                        if len(agg)>=60:frames[f'{mins}m']=engine.technical_snapshot(agg)
                    except Exception as exc:app.log.warning('CANDICE OWN BRAIN 24/7 FRAME FAILED pair=%s tf=%sm: %s',asset,mins,exc)
                ctx=app.summary(asset) if hasattr(app,'summary') else {}
                learning=(ctx or {}).get('learning',{}) if isinstance(ctx,dict) else {}
                recent=learning.get('recent_outcomes',[]) if isinstance(learning,dict) else []
                recovery=bool(recent and str(recent[-1].get('result','')).upper()=='LOSS')
                plans={}
                for tf,snap in frames.items():
                    try:plans[tf]=build_plan(snap,frames,snap.get('candle_patterns',()))
                    except Exception as exc:app.log.warning('CANDICE OWN BRAIN 24/7 PLAN FAILED pair=%s tf=%s: %s',asset,tf,exc)
                candidates=[]
                for tf,plan in plans.items():
                    score=plan_score(frames,tf,plan,recovery)
                    if score>=0:candidates.append((score,tf,plan))
                candidates.sort(key=lambda x:x[0],reverse=True)
                best=candidates[0] if candidates else None
                if best:
                    direction=str(best[2].next_candle_direction).upper()
                    aligned=sum(str(v.get('direction','')).upper()==direction for v in frames.values())
                    required=4 if recovery else 3
                    if aligned<required:best=None
                if best:
                    score,tf,plan=best
                    app.log.info('CANDICE OWN BRAIN 24/7 BEST pair=%s tf=%s direction=%s score=%.1f expiry=%s recovery=%s aligned=%s',asset,tf,plan.next_candle_direction,score,plan.recommended_expiry,recovery,sum(str(v.get('direction','')).upper()==str(plan.next_candle_direction).upper() for v in frames.values()))
                else:
                    app.log.info('CANDICE OWN BRAIN 24/7 WAIT pair=%s reason=no-qualified-timeframe recovery=%s',asset,recovery)
                try:app.record({'ts':time.time(),'type':'brain_247_observation','asset':asset,'candle_ts':candle_ts,'timeframes':list(frames),'best_timeframe':best[1] if best else '','direction':best[2].next_candle_direction if best else 'WAIT','pattern':getattr(best[2],'pattern','') if best else '','situation':getattr(best[2],'situation','') if best else '','expiry':getattr(best[2],'recommended_expiry',0) if best else 0,'wait':best is None,'recovery_mode':recovery})
                except Exception as exc:app.log.warning('CANDICE OWN BRAIN 24/7 MEMORY OBSERVATION FAILED pair=%s: %s',asset,exc)
            except asyncio.CancelledError:raise
            except Exception:app.log.exception('CANDICE OWN BRAIN 24/7 ASSET WATCH FAILED pair=%s',asset)

    async def once():
        try:assets=list(await asyncio.wait_for(app.broker.live_assets(),timeout=FETCH_TIMEOUT) or [])
        except asyncio.TimeoutError:app.log.warning('CANDICE OWN BRAIN 24/7 LIVE ASSET TIMEOUT');return
        except Exception:app.log.exception('CANDICE OWN BRAIN 24/7 LIVE ASSET FAILED');return
        app.log.info('CANDICE OWN BRAIN 24/7 TICK assets=%d selected=%d concurrency=%d',len(assets),len(getattr(app,'selected',{}) or {}),MAX_CONCURRENCY)
        if not assets:return
        sem=asyncio.Semaphore(MAX_CONCURRENCY)
        await asyncio.gather(*(observe(a,sem) for a in assets))

    async def loop_body():
        app.log.info('CANDICE OWN BRAIN 24/7 LOOP STARTED — ALL LIVE ASSETS — 1M/2M/3M/5M/10M/15M')
        while True:
            started=time.time()
            try:await once()
            except asyncio.CancelledError:raise
            except Exception:app.log.exception('CANDICE OWN BRAIN 24/7 WATCH FAILED')
            nxt=(int(time.time()//60)+1)*60+2
            sleep=max(1,nxt-time.time())
            app.log.info('CANDICE OWN BRAIN 24/7 CYCLE DONE elapsed=%.2fs next_in=%.2fs',time.time()-started,sleep)
            await asyncio.sleep(sleep)

    def schedule():
        try:loop=asyncio.get_running_loop()
        except RuntimeError:return False
        if app._candice_own_brain_247_task is None:
            app._candice_own_brain_247_task=loop.create_task(loop_body());app.log.info('CANDICE OWN BRAIN 24/7 ACTIVE — INDEPENDENT TASK SCHEDULED')
        return True
    if schedule():return
    loop=getattr(app,'BOT_LOOP',None)
    if loop is not None:
        try:
            asyncio.run_coroutine_threadsafe(_retry(schedule),loop);app.log.info('CANDICE OWN BRAIN 24/7 STARTUP RETRY SCHEDULED');return
        except Exception as exc:app.log.warning('CANDICE OWN BRAIN 24/7 RETRY SCHEDULE FAILED: %s',exc)
    app.log.warning('CANDICE OWN BRAIN 24/7 WAITING FOR EVENT LOOP — TELEGRAM NOT REQUIRED')

async def _retry(schedule_fn):
    for _ in range(180):
        if schedule_fn():return
        await asyncio.sleep(1)

from __future__ import annotations
import asyncio, time

FETCH_TIMEOUT=25
MAX_CONCURRENCY=3


def install(app):
    """Independent 24/7 closed-1m observation loop; never places trades."""
    if getattr(app,'_candice_own_brain_247_task',None) is not None:return
    import candice_engine as engine
    from candice_strategy_v4 import build_plan
    app._candice_brain_247_last_minute={}
    app._candice_own_brain_247_task=None

    def ts_of(v):
        try:return float(v.timestamp()) if hasattr(v,'timestamp') else float(v)
        except Exception:return None

    async def observe(asset,sem):
        async with sem:
            try:
                app.log.info('CANDICE OWN BRAIN 24/7 FETCH START pair=%s',asset)
                try:
                    df,err=await asyncio.wait_for(app.broker.candles(asset,60,max(120,engine.ANALYSIS_CANDLE_COUNT),360),timeout=FETCH_TIMEOUT)
                except asyncio.TimeoutError:
                    app.log.warning('CANDICE OWN BRAIN 24/7 FETCH TIMEOUT pair=%s',asset);return
                n=len(df) if df is not None else 0
                app.log.info('CANDICE OWN BRAIN 24/7 FETCH DONE pair=%s rows=%s err=%s',asset,n,err or '')
                if err or df is None or n<60:
                    app.log.info('CANDICE OWN BRAIN 24/7 WAIT pair=%s reason=%s',asset,err or 'insufficient candles');return
                closed=engine.closed_1m(df)
                if len(closed)<60:
                    app.log.info('CANDICE OWN BRAIN 24/7 WAIT pair=%s reason=insufficient closed candles count=%s',asset,len(closed));return
                raw_index=closed.index[-1]; candle_ts=ts_of(closed.iloc[-1].get('timestamp'))
                if candle_ts is None:return
                minute=int(candle_ts//60)
                if app._candice_brain_247_last_minute.get(asset)==minute:return
                app._candice_brain_247_last_minute[asset]=minute
                app.log.info('CANDICE OWN BRAIN 24/7 CLOSED READY pair=%s candle_ts=%s',asset,candle_ts)
                frames={'1m':engine.technical_snapshot(closed)}
                for mins in (3,5,10,15):
                    try:
                        agg=engine.resample_ohlc(closed,mins)
                        # Higher-TF snapshots need 60 bars: request history is
                        # intentionally deep enough to build them when available.
                        if len(agg)>=60:frames[f'{mins}m']=engine.technical_snapshot(agg)
                    except Exception as exc:app.log.warning('CANDICE OWN BRAIN 24/7 FRAME FAILED pair=%s tf=%sm: %s',asset,mins,exc)
                primary=frames.get('5m',frames['1m']);plan=build_plan(primary,frames,primary.get('candle_patterns',()))
                app.log.info('CANDICE OWN BRAIN 24/7 PLAN READY pair=%s direction=%s pattern=%s situation=%s next=%s/%s expiry=%s wait=%s',asset,frames['1m'].get('direction',''),getattr(plan,'pattern',''),getattr(plan,'situation',''),getattr(plan,'next_candle_direction',''),getattr(plan,'next_candle_timeframe',''),getattr(plan,'recommended_expiry',0),getattr(plan,'wait',True))
                try:app.record({'ts':time.time(),'type':'brain_247_observation','asset':asset,'candle_ts':candle_ts,'direction':frames['1m'].get('direction',''),'pattern':getattr(plan,'pattern',''),'situation':getattr(plan,'situation',''),'next_candle_direction':getattr(plan,'next_candle_direction',''),'next_candle_timeframe':getattr(plan,'next_candle_timeframe',''),'expiry':getattr(plan,'recommended_expiry',0),'wait':bool(getattr(plan,'wait',True))})
                except Exception as exc:app.log.warning('CANDICE OWN BRAIN 24/7 MEMORY OBSERVATION FAILED pair=%s: %s',asset,exc)
                app.log.info('CANDICE OWN BRAIN 24/7 CANDLE pair=%s candle=%s direction=%s pattern=%s situation=%s next=%s/%s expiry=%s wait=%s',asset,str(closed.iloc[-1].get('timestamp')),frames['1m'].get('direction',''),getattr(plan,'pattern',''),getattr(plan,'situation',''),getattr(plan,'next_candle_direction',''),getattr(plan,'next_candle_timeframe',''),getattr(plan,'recommended_expiry',0),getattr(plan,'wait',True))
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
        app.log.info('CANDICE OWN BRAIN 24/7 LOOP STARTED — INDEPENDENT CLOSED 1M OBSERVATION')
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
    # No Telegram dependency; supervisor logs the terminal failure.

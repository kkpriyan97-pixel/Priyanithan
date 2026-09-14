from __future__ import annotations

import asyncio
import time

EXPIRIES = (1, 2, 3, 5, 10, 15)
TIMEFRAMES = ("1m", "2m", "3m", "5m", "10m", "15m")


def install(app):
    """Candice decision layer: scan every live asset, evaluate 1m/2m/3m/5m/10m/15m,
    choose the strongest qualified context, warn 60s before a checkpoint, then
    revalidate at the boundary. Manual/demo only; never places trades."""
    if getattr(app, "_candice_multi_tf_v1", False):
        return

    import candice_engine as engine
    from candice_strategy_v4 import build_plan
    from app import Signal

    pending = {}
    sem = asyncio.Semaphore(3)
    original_analyze = getattr(app, "analyze", None)

    def tf_score(snapshot, plan, tf, frames, recovery=False):
        direction = str(getattr(plan, "next_candle_direction", "")).upper()
        if direction not in {"UP", "DOWN"} or getattr(plan, "wait", True):
            return -1.0
        dirs = [str(v.get("direction", "")).upper() for v in frames.values()]
        aligned = sum(d == direction for d in dirs)
        strength = float(snapshot.get("strength", 0) or 0)
        adx = float(snapshot.get("adx14", 0) or 0)
        agreement = float(snapshot.get("indicator_agreement", 0) or 0)
        body = float(snapshot.get("body_ratio", 0) or 0)
        score = aligned * 10.0 + min(strength, 1.0) * 18.0 + min(adx, 40.0) * 0.45 + min(agreement, 5.0) * 2.5 + min(body, 1.0) * 5.0
        if tf in ("10m", "15m"):
            score += 4.0
        if tf == "2m":
            score += 1.0
        if recovery:
            score += 3.0 if aligned >= 4 else -8.0
        return score

    async def build_multiframe(asset):
        try:
            df, err = await asyncio.wait_for(app.broker.candles(asset, 60, 1000, 360), timeout=30)
            if err or df is None or len(df) < 120:
                return None, err or "insufficient candles"
            closed = engine.closed_1m(df)
            if len(closed) < 120:
                return None, f"insufficient closed candles ({len(closed)})"
            frames = {"1m": engine.technical_snapshot(closed)}
            for mins in (2, 3, 5, 10, 15):
                try:
                    agg = engine.resample_ohlc(closed, mins)
                    if len(agg) >= 60:
                        frames[f"{mins}m"] = engine.technical_snapshot(agg)
                except Exception as exc:
                    app.log.warning("CANDICE MULTI-TF FRAME FAILED pair=%s tf=%sm error=%s", asset, mins, exc)
            ctx = app.summary(asset) if hasattr(app, "summary") else {}
            learning = (ctx or {}).get("learning", {}) if isinstance(ctx, dict) else {}
            recent = learning.get("recent_outcomes", []) if isinstance(learning, dict) else []
            recovery = bool(recent and str(recent[-1].get("result", "")).upper() == "LOSS")
            plans = {}
            for tf, snap in frames.items():
                try:
                    plans[tf] = build_plan(snap, frames, snap.get("candle_patterns", ()))
                except Exception as exc:
                    app.log.warning("CANDICE MULTI-TF PLAN FAILED pair=%s tf=%s error=%s", asset, tf, exc)
            candidates = []
            for tf, plan in plans.items():
                score = tf_score(frames[tf], plan, tf, frames, recovery)
                if score >= 0:
                    candidates.append((score, tf, plan))
            candidates.sort(key=lambda x: x[0], reverse=True)
            best = candidates[0] if candidates else None
            directions = [str(v.get("direction", "")).upper() for v in frames.values()]
            if best:
                best_direction = str(best[2].next_candle_direction).upper()
                aligned = sum(d == best_direction for d in directions)
                required = 4 if recovery else 3
                if aligned < required:
                    best = None
            return {"frames": frames, "plans": plans, "best": best, "recovery": recovery}, None
        except Exception as exc:
            return None, str(exc)

    async def choose_candidate(asset):
        multi, err = await build_multiframe(asset)
        if multi is None:
            return None
        best = multi.get("best")
        if best is None:
            app.log.info("CANDICE MULTI-TF REJECT pair=%s reason=no-qualified-timeframe recovery=%s", asset, multi.get("recovery"))
            return None
        score, tf, plan = best
        ai = await original_analyze(asset, app.broker, app.summary(asset)) if original_analyze else None
        if ai is None or str(getattr(ai, "decision", "REJECT")).upper() != "APPROVE":
            app.log.info("CANDICE MULTI-TF REJECT pair=%s reason=AI-gate direction=%s tf=%s", asset, getattr(ai, "direction", ""), tf)
            return None
        direction = str(getattr(ai, "direction", "")).upper()
        if direction not in {"UP", "DOWN"} or direction != str(plan.next_candle_direction).upper():
            app.log.info("CANDICE MULTI-TF REJECT pair=%s reason=AI/TIMEFRAME-DIRECTION-CONFLICT ai=%s tf=%s", asset, direction, plan.next_candle_direction)
            return None
        expiry = int(getattr(plan, "recommended_expiry", 0) or 0)
        if expiry not in EXPIRIES:
            expiry = int(getattr(ai, "expiry", 0) or 0)
        if expiry not in EXPIRIES:
            return None
        confidence = int(getattr(ai, "confidence", 0) or 0)
        return {
            "asset": asset, "direction": direction, "confidence": confidence,
            "expiry": expiry, "timeframe": tf, "score": score,
            "evidence": tuple(getattr(ai, "evidence", ()) or ()) + tuple(getattr(plan, "reasons", ()) or ())[:2],
            "reason": str(getattr(ai, "reason", "Qualified after multi-timeframe review")),
            "recovery": multi.get("recovery", False), "frames": multi.get("frames", {}),
        }

    async def all_assets_best():
        try:
            assets = list(await asyncio.wait_for(app.broker.live_assets(), timeout=30) or [])
        except Exception as exc:
            app.log.warning("CANDICE ALL-ASSET SCAN FAILED error=%s", exc)
            return None
        if not assets:
            return None
        async def one(asset):
            async with sem:
                try:
                    return await choose_candidate(asset)
                except Exception:
                    app.log.exception("CANDICE ALL-ASSET CANDIDATE FAILED pair=%s", asset)
                    return None
        results = [x for x in await asyncio.gather(*(one(a) for a in assets)) if x]
        if not results:
            app.log.info("CANDICE ALL-ASSET SCAN — no qualified candidate among %d assets", len(assets))
            return None
        results.sort(key=lambda x: (x["confidence"], x["score"]), reverse=True)
        best = results[0]
        app.log.info("CANDICE ALL-ASSET BEST asset=%s direction=%s confidence=%s tf=%s expiry=%s score=%.1f candidates=%d recovery=%s", best["asset"], best["direction"], best["confidence"], best["timeframe"], best["expiry"], best["score"], len(results), best["recovery"])
        return best

    async def patched_scan_once():
        now = time.time()
        minute = int(now // 60)
        slot = (minute % 5) == 4
        if not slot:
            return
        best = await all_assets_best()
        if best is None:
            pending.clear()
            return
        for uid in list(getattr(app, "users", set()) or set()):
            if uid in app.active:
                continue
            pending[uid] = best
            text = (
                f'{app.header("1-MINUTE SIGNAL PRE-ALERT")}\n\n'
                f'🏆 BEST LIVE ASSET • {best["asset"]}\n'
                f'➡️ DIRECTION • {best["direction"]}\n'
                f'🧠 BEST STRATEGY TF • {best["timeframe"]}\n'
                f'⏱️ EXPIRY • {best["expiry"]} MIN\n'
                f'📊 AI CONFIDENCE • {best["confidence"]}%\n\n'
                f'⏳ SIGNAL CHECK • 01:00\n'
                f'🔍 ALL LIVE ASSETS + 1m/2m/3m/5m/10m/15m CHECKED\n\n'
                f'⚠️ PRE-ALERT ONLY — final signal requires fresh re-check.\n'
                f'🛡️ DEMO / MANUAL ONLY • AUTO-TRADE OFF'
            )
            await app.send_text(uid, text)
        app.log.info("CANDICE 1-MIN PRE-ALERT SENT asset=%s users=%d", best["asset"], len(pending))

    async def boundary_scan():
        minute = int(time.time() // 60)
        if (minute % 5) != 0:
            return
        best_by_user = list(pending.items())
        pending.clear()
        for uid, old in best_by_user:
            if uid in app.active:
                continue
            fresh = await choose_candidate(old["asset"])
            if fresh is None:
                app.log.info("CANDICE 1-MIN RECHECK REJECT asset=%s reason=final-validation", old["asset"])
                continue
            if fresh["direction"] != old["direction"]:
                app.log.info("CANDICE 1-MIN RECHECK REJECT asset=%s reason=direction-changed", old["asset"])
                continue
            df, err = await app.broker.candles(old["asset"], 60, 120, 360)
            if err or df is None or df.empty:
                continue
            row = app.broker.closed_candle(df, time.time(), 60)
            if row is None:
                continue
            entry = float(row.close)
            candle_ts = float(row.timestamp)
            entry_ts = float((int(time.time()) // 60) * 60)
            msg = await app.send_signal_card(uid, fresh["asset"], fresh["direction"], entry_ts, candle_ts, fresh["expiry"], fresh["confidence"], fresh["timeframe"], fresh["evidence"])
            if msg is None:
                continue
            s = Signal(fresh["asset"], fresh["direction"], fresh["confidence"], fresh["expiry"], entry, entry_ts, candle_ts, fresh["timeframe"], fresh["reason"], fresh["evidence"])
            app.active[uid] = s
            asyncio.create_task(app.result_monitor(uid, s, msg))
            app.log.info("CANDICE FINAL SIGNAL asset=%s direction=%s tf=%s expiry=%s confidence=%s", fresh["asset"], fresh["direction"], fresh["timeframe"], fresh["expiry"], fresh["confidence"])

    async def patched_scheduler():
        while True:
            now = time.time()
            next_tick = (int(now // 60) + 1) * 60
            await asyncio.sleep(max(0.2, next_tick - now))
            try:
                minute = int(time.time() // 60)
                if minute % 5 == 4:
                    await patched_scan_once()
                elif minute % 5 == 0:
                    await boundary_scan()
            except Exception:
                app.log.exception("CANDICE MULTI-TF SCHEDULER FAILED")

    app.scan_once = patched_scan_once
    app.scheduler = patched_scheduler
    app._candice_multi_tf_v1 = True
    app.log.info("CANDICE MULTI-TF V1 ACTIVE — ALL LIVE ASSETS + 1m/2m/3m/5m/10m/15m + 60S PRE-ALERT + BOUNDARY RECHECK")

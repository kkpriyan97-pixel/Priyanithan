"""Candice final runtime guard: evolution wiring + outcome learning + live health proof.

DEMO/MANUAL ONLY. Never places or executes trades.
"""
from __future__ import annotations

import asyncio
import os
import time


def install(app):
    if getattr(app, "_candice_final_guard_v1", False):
        return

    try:
        import candice_strategy_evolution as evolution
    except Exception as exc:
        app.log.exception("CANDICE FINAL GUARD EVOLUTION IMPORT FAILED: %s", exc)
        evolution = None

    strategy_by_asset = {}
    original_analyze = getattr(app, "analyze", None)
    original_record = getattr(app, "record", None)

    if original_analyze is not None and evolution is not None:
        async def guarded_analyze(asset, broker_obj, memory_context):
            result = await original_analyze(asset, broker_obj, memory_context)
            try:
                regime = str((memory_context or {}).get("regime") or "unknown")
                # Evolution runs continuously in shadow mode. It never overrides V4/AI gates.
                ev = evolution.evolve(regime)
                plan = getattr(result, "__dict__", {})
                plan["evolution_strategy_id"] = ev.get("strategy_id", "")
                plan["evolution_score"] = ev.get("score", 0.0)
                plan["evolution_samples"] = ev.get("sample_count", 0)
                plan["evolution_win_rate"] = ev.get("win_rate", 0.0)
                plan["evolution_candidate_created"] = ev.get("candidate_created", "")
                strategy_by_asset[asset] = dict(plan)
                app.log.info(
                    "CANDICE STRATEGY EVOLUTION APPLIED pair=%s strategy=%s score=%.4f samples=%s win_rate=%.3f candidate=%s decision=%s",
                    asset, ev.get("strategy_id", ""), float(ev.get("score", 0.0) or 0.0),
                    ev.get("sample_count", 0), float(ev.get("win_rate", 0.0) or 0.0),
                    ev.get("candidate_created", ""), getattr(result, "decision", ""),
                )
            except Exception as exc:
                app.log.warning("CANDICE STRATEGY EVOLUTION APPLY FAILED pair=%s: %s", asset, exc)
            return result
        app.analyze = guarded_analyze

    if original_record is not None and evolution is not None:
        def guarded_record(event):
            try:
                if isinstance(event, dict):
                    outcome = str(event.get("result", "")).upper()
                    asset = str(event.get("asset", ""))
                    sid = str(strategy_by_asset.get(asset, {}).get("evolution_strategy_id", ""))
                    if outcome in {"WIN", "LOSS"} and sid:
                        evolution.record_outcome(
                            sid,
                            outcome,
                            regime=str(event.get("regime", "unknown")),
                            asset=asset,
                        )
                        app.log.info(
                            "CANDICE STRATEGY OUTCOME pair=%s strategy=%s outcome=%s",
                            asset, sid, outcome,
                        )
            except Exception as exc:
                app.log.warning("CANDICE STRATEGY OUTCOME LEARNING FAILED: %s", exc)
            return original_record(event)
        app.record = guarded_record

    async def heartbeat():
        while True:
            try:
                broker_ok = bool(getattr(app, "broker", None) and app.broker.connected())
                ai_ok = bool(getattr(app, "_candice_ai_fallback_v9", False))
                brain_ok = bool(getattr(app, "_candice_own_strategy_v4", False))
                checkpoint_ok = bool(getattr(app, "_candice_checkpoint_v2", False))
                timer_ok = bool(getattr(app, "_candice_ready_timer_v13", False))
                evo_ok = evolution is not None
                app.log.info(
                    "CANDICE FINAL HEALTH broker=%s ai_fallback=%s brain_v4=%s checkpoint=%s ready_timer=%s evolution=%s auto_trade=%s martingale=%s",
                    "OK" if broker_ok else "WAIT",
                    "OK" if ai_ok else "MISSING",
                    "OK" if brain_ok else "MISSING",
                    "OK" if checkpoint_ok else "MISSING",
                    "OK" if timer_ok else "MISSING",
                    "OK" if evo_ok else "MISSING",
                    getattr(app, "AUTO_TRADE", False), getattr(app, "MARTINGALE", False),
                )
                if evo_ok:
                    try:
                        ev = evolution.evolve("heartbeat")
                        app.log.info(
                            "CANDICE STRATEGY ROTATION regime=heartbeat strategy=%s score=%.4f samples=%s win_rate=%.3f candidate=%s",
                            ev.get("strategy_id", ""), float(ev.get("score", 0.0) or 0.0),
                            ev.get("sample_count", 0), float(ev.get("win_rate", 0.0) or 0.0),
                            ev.get("candidate_created", ""),
                        )
                    except Exception as exc:
                        app.log.warning("CANDICE STRATEGY ROTATION FAILED: %s", exc)
            except Exception as exc:
                app.log.warning("CANDICE FINAL HEALTH FAILED: %s", exc)
            await asyncio.sleep(max(60, int(os.getenv("CANDICE_HEALTH_INTERVAL", "60"))))

    try:
        loop = getattr(app, "BOT_LOOP", None)
        if loop is not None and loop.is_running():
            app._candice_final_health_task = asyncio.run_coroutine_threadsafe(heartbeat(), loop)
        else:
            app._candice_final_health_task = None
            app.log.warning("CANDICE FINAL HEALTH TASK WAITING — BOT_LOOP not ready")
    except Exception as exc:
        app.log.warning("CANDICE FINAL HEALTH TASK START FAILED: %s", exc)

    app._candice_final_guard_v1 = True
    app.log.info("CANDICE FINAL GUARD V1 ACTIVE — EVOLUTION→OUTCOME LEARNING→HEALTH PROOF — DEMO/MANUAL ONLY")

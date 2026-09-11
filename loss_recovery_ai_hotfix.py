"""Canonical one-shot same-pair recovery after a verified LOSS.

This module owns the single result lifecycle monitor. It waits until the memory
integration is ready, then patches monitor_result last so memory cannot replace
the recovery flow. Recovery is one attempt only, with strict 1/2 minute manual
signals. No Martingale, no broker orders, no forced signal.
"""
import asyncio
import threading
import time

PATCHED = False
RECOVERY_DURATIONS = (1, 2)
RECOVERY_MAX_WAIT = 120
RECOVERY_MIN_CONF = 72


def _app():
    import sys
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


async def _candice_recovery(a, pair, df1, df5, tech):
    """Use the existing AI provider with a recovery-specific strict prompt."""
    if not getattr(a, "OPENROUTER_API_KEY", ""):
        return None, "Candice AI provider unavailable"
    prompt = (
        "You are Candice, a conservative short-horizon trading analyst. "
        "This is ONE recovery attempt after a verified loss on the SAME pair. "
        "Do not revenge-trade and do not force a signal. Analyze only the fresh "
        "1-minute and 5-minute structure supplied below. Approve only when the "
        "direction has clear immediate momentum, candle confirmation, RSI is not "
        "extreme against the direction, and the 5m context does not conflict. "
        "The recovery duration MUST be exactly 1 or 2 minutes. Prefer 1 minute "
        "only when immediate structure is exceptionally clear; otherwise use 2. "
        "Return compact JSON with decision, direction, confidence, duration_min, reason. "
        "Never invent data.\n\n"
        f"Pair: {pair}\nTechnical direction: {tech.get('signal')}\n"
        f"Technical confidence: {tech.get('confidence')}\n5m trend: {tech.get('trend_5m')}\n"
        f"Technical reason: {tech.get('reason')}\nFresh 1m candles: {len(df1)}\nFresh 5m candles: {len(df5)}\n"
    )
    try:
        response = await asyncio.to_thread(
            a.requests.post,
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {a.OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={"model": a.OPENROUTER_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
            timeout=20,
        )
        content = response.json()["choices"][0]["message"]["content"]
        import json, re
        raw = str(content).strip()
        try:
            data = json.loads(raw)
        except Exception:
            match = re.search(r"\{.*\}", raw, re.S)
            if not match:
                return None, "AI response was not valid JSON"
            data = json.loads(match.group(0))
        decision = str(data.get("decision", "")).upper()
        direction = str(data.get("direction", "")).upper()
        confidence = int(data.get("confidence", 0))
        duration = int(data.get("duration_min", 0))
        if decision != "APPROVE": return None, "Candice rejected recovery setup"
        if direction not in ("UP", "DOWN") or direction != str(tech.get("signal", "")).upper():
            return None, "recovery direction disagrees with technical structure"
        if confidence < RECOVERY_MIN_CONF: return None, f"recovery confidence {confidence}% below {RECOVERY_MIN_CONF}%"
        if duration not in RECOVERY_DURATIONS: return None, "recovery duration must be 1 or 2 minutes"
        return {"direction": direction, "confidence": confidence, "duration": duration, "reason": str(data.get("reason", ""))[:220]}, None
    except Exception as exc:
        return None, f"Candice recovery error: {str(exc)[:140]}"


async def _find_recovery(a, pair):
    """Re-check the same pair for up to two minutes; never force a signal."""
    deadline = time.time() + RECOVERY_MAX_WAIT
    last_reason = "no qualified short recovery setup"
    while time.time() < deadline:
        try:
            df1, e1 = await a.get_candles(pair, 60, 80, a.LIVE_1M_MAX_AGE)
            df5, e5 = await a.get_candles(pair, 300, 80, a.LIVE_5M_MAX_AGE)
            if df1 is None or df5 is None:
                last_reason = e1 or e5 or "fresh candles unavailable"
            else:
                tech = a.technical_analysis(pair, df1, df5)
                if tech.get("signal") == "NO SIGNAL": last_reason = "mixed technical structure"
                elif tech.get("confidence", 0) < 70: last_reason = "technical confidence below recovery threshold"
                elif str(tech.get("trend_5m", "")).upper() != str(tech.get("signal", "")).upper(): last_reason = "5m context conflicts"
                else:
                    ai, err = await _candice_recovery(a, pair, df1, df5, tech)
                    if ai:
                        return {"pair": pair, "direction": ai["direction"], "price": float(df1["close"].iloc[-1]), "duration": ai["duration"], "created_at": time.time(), "ai_confidence": ai["confidence"], "confidence": int(tech.get("confidence", 0)), "trend_5m": tech.get("trend_5m", "UNKNOWN"), "candle_time": a.fmt_ts(float(df1["timestamp"].iloc[-1])), "ai_reason": "SHORT RECOVERY: " + ai["reason"], "recovery": True}, None
                    last_reason = err or "Candice rejected recovery"
        except Exception as exc:
            last_reason = str(exc)[:160]
        await asyncio.sleep(5)
    return None, last_reason


def _record_result(a, signal, result, expiry):
    recorder = getattr(a, "record_candice_result", None)
    if callable(recorder):
        try:
            recorder(signal, result, expiry)
        except Exception as exc:
            a.log.warning("CANDICE RESULT MEMORY HOOK FAILED: %s", exc)


async def _recovery_result(a, bot, uid, signal):
    expiry_ts = float(signal.get("created_at", time.time())) + int(signal["duration"]) * 60
    await asyncio.sleep(max(1.0, expiry_ts - time.time()))
    expiry, expiry_price_ts, source = await a.get_expiry_price(signal["pair"], expiry_ts)
    if expiry is None:
        await a.send_text(bot, f"⚠️ RECOVERY RESULT UNRESOLVED — {signal['pair']}\nNo result was guessed.\n🔁 Returning to new asset selection.", uid)
        a.active_signal.pop(uid, None); a.selected_asset.pop(uid, None)
        await a.send_asset_menu(bot, uid, "🔁 Recovery unresolved → select a fresh asset.")
        return
    result = a.verify_result(float(signal["price"]), expiry, signal["direction"])
    _record_result(a, signal, result, expiry)
    await a.send_text(bot, "📊 SHORT RECOVERY RESULT\n\n" f"📈 {signal['pair']}\n" f"{'⬆️' if signal['direction']=='UP' else '⬇️'} {signal['direction']}\n" f"💰 Entry: {a.fmt_price(signal['price'])}\n" f"🏁 Expiry: {a.fmt_price(expiry)}\n" f"⏱️ Duration: {signal['duration']} MIN\n" f"🔎 Verification: {source}\n\n{'✅' if result=='WIN' else '❌' if result=='LOSS' else '➖'} {result}\n\n⚠️ RECOVERY IS ONE-SHOT — AUTO TRADE OFF", uid)
    a.active_signal.pop(uid, None); a.selected_asset.pop(uid, None)
    await a.send_asset_menu(bot, uid, "🔁 Recovery cycle complete → select a fresh asset.")


async def _patched_monitor_result(bot, uid, signal):
    a = _app()
    # A recovery result monitor is already active only when the signal is marked
    # recovery. Normal signals enter this function exactly once.
    expiry_ts = float(signal.get("created_at", time.time())) + int(signal["duration"]) * 60
    await asyncio.sleep(max(1.0, expiry_ts - time.time()))
    expiry, expiry_price_ts, source = await a.get_expiry_price(signal["pair"], expiry_ts)
    if expiry is None:
        await a.send_text(bot, f"⚠️ RESULT UNRESOLVED — {signal['pair']}\nExpiry boundary price unavailable: {source}\nNo result was guessed.", uid)
        a.active_signal.pop(uid, None)
        a.selected_asset.pop(uid, None)
        await a.send_asset_menu(bot, uid, "⚠️ Result unresolved → select a fresh asset.")
        return
    result = a.verify_result(float(signal["price"]), expiry, signal["direction"])
    _record_result(a, signal, result, expiry)
    await a.send_text(bot, "📊 TRADE RESULT\n\n" f"📈 {signal['pair']}\n" f"{'⬆️' if signal['direction']=='UP' else '⬇️' if signal['direction']=='DOWN' else '➡️'} {signal['direction']}\n" f"💰 Entry: {a.fmt_price(signal['price'])}\n" f"🏁 Expiry: {a.fmt_price(expiry)}\n" f"⏱️ Duration: {signal['duration']} MIN\n" f"🕐 Expiry boundary: {a.fmt_ts(expiry_ts)}\n" f"🔎 Verification: {source}\n" f"📌 Price candle: {a.fmt_ts(expiry_price_ts)}\n\n" f"{'✅' if result=='WIN' else '❌' if result=='LOSS' else '➖'} {result}\n\n⚠️ RESULT ONLY — AUTO TRADE OFF", uid)
    a.active_signal.pop(uid, None)

    if result == "LOSS" and not signal.get("recovery", False):
        pair = str(signal["pair"]).upper()
        a.active_signal[uid] = {"recovery_pending": True, "pair": pair}
        await a.send_text(bot, "🔴 LOSS CONFIRMED\n\n🛡️ SHORT RECOVERY MODE\n📈 Same pair: " + pair + "\n⏱️ AI window: next 120 seconds\n🎯 Duration: 1 or 2 MIN\n\nCandice is re-checking fresh 1m + 5m structure.\nNo forced signal. No martingale.\n\n⚠️ MANUAL TRADE ONLY — AUTO TRADE OFF", uid)
        recovery, err = await _find_recovery(a, pair)
        a.active_signal.pop(uid, None)
        if recovery:
            a.active_signal[uid] = recovery
            await a.send_signal(bot, uid, recovery)
            asyncio.create_task(_recovery_result(a, bot, uid, recovery), name=f"recovery-result-{uid}-{pair}")
            return
        a.selected_asset.pop(uid, None)
        await a.send_asset_menu(bot, uid, "⚠️ No safe short recovery setup → select a fresh asset.")
        return

    a.selected_asset.pop(uid, None)
    if signal.get("recovery"):
        note = "✅ Recovery WIN → select a fresh asset." if result == "WIN" else "❌ Recovery ended → select a fresh asset."
    else:
        note = "✅ WIN → select a fresh asset." if result == "WIN" else "➖ DRAW → select a fresh asset."
    await a.send_asset_menu(bot, uid, note)


def _patch():
    global PATCHED
    a = _app()
    if not a or getattr(a, "_LOSS_RECOVERY_AI_V2", False):
        return bool(a)
    # Memory must finish installing its analyze/send hooks first. This removes
    # the previous race where memory replaced this monitor and silently disabled
    # recovery after LOSS.
    if not getattr(a, "_CANDICE_MEMORY_INTEGRATION_V3", False):
        return False
    if not callable(getattr(a, "monitor_result", None)):
        return False
    a.monitor_result = _patched_monitor_result
    a._LOSS_RECOVERY_AI_V2 = True
    a.log.warning("LOSS RECOVERY AI V2 ACTIVE: canonical result monitor + one-shot same-pair recovery")
    PATCHED = True
    return True


def _boot():
    for _ in range(1800):
        try:
            if _patch():
                return
        except Exception:
            pass
        time.sleep(0.1)

threading.Thread(target=_boot, name="loss-recovery-ai", daemon=True).start()

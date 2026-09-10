"""Priyanithan signal engine.

Classic compact Telegram format with live technical + AI confirmation.
AUTO TRADE remains OFF; this module never places broker orders.
"""
import asyncio
import sys
import time

EXPIRIES = (1, 2, 3, 5, 10, 15)


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _classic_signal_text(result, ai):
    direction = str(ai.get("direction", "")).upper()
    if direction not in ("UP", "DOWN"):
        return None
    arrow = "⬆️" if direction == "UP" else "⬇️"
    try:
        expiry = int(ai.get("duration_min", 5))
    except Exception:
        expiry = 5
    if expiry not in EXPIRIES:
        expiry = 5
    confidence = int(ai.get("confidence", 0))
    technical = int(result.get("confidence", 0))
    return (
        "🔥 PRIYANITHAN AI SIGNAL 🔥\n\n"
        f"📈 {result['pair']}\n\n"
        f"{arrow} {direction}\n\n"
        f"💰 Entry: {result.get('price', 'LIVE')}\n\n"
        f"⏱️ Expiry: {expiry} MIN\n\n"
        f"🤖 AI Confidence: {confidence}%\n\n"
        f"📊 Technical: {technical}%\n\n"
        f"🕐 {time.strftime('%H:%M:%S')} UAE\n\n"
        "🧠 Candice AI: APPROVED\n\n"
        "⚠️ MANUAL TRADE — AUTO TRADE OFF"
    )


async def _wait_for_live_assets(a, timeout=45):
    """Wait for the broker's 1054 instrument catalogue before scanning.

    /start can arrive immediately after the service boots. Previously the
    scanner could run before instrument discovery completed, producing a
    false-looking NO QUALIFIED SIGNAL even though no assets had been scanned.
    """
    if not getattr(a, "AUTO_DISCOVER_ASSETS", False):
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        assets = getattr(a, "discovered_assets", {})
        client = getattr(a, "ot_client", None)
        if assets and client is not None:
            return True
        await asyncio.sleep(1)
    return bool(getattr(a, "discovered_assets", {}))


def _install():
    a = _app()
    if not a or getattr(a, "_CLASSIC_SIGNAL_ENGINE", False):
        return bool(a)
    if not getattr(a, "scan_cycle", None):
        return False

    async def classic_scan(application):
        ready = await _wait_for_live_assets(a)
        if not ready:
            a.log.warning("CLASSIC SCAN ABORTED: live broker asset catalogue not ready")
            await a.send_to_recipients(
                application.bot,
                "⚠️ LIVE MARKET DATA NOT READY\n\n"
                "Olymptrade asset discovery is still connecting.\n"
                "⏱️ The next automatic 5-minute scan will retry."
            )
            return

        universe = (
            sorted(a.discovered_assets.keys())[:a.MAX_ASSETS_PER_CYCLE]
            if a.AUTO_DISCOVER_ASSETS
            else (a.MANUAL_PAIRS[:] if a.MANUAL_PAIRS else a.PAIRS[:a.MAX_ASSETS_PER_CYCLE])
        )
        a.log.info("CLASSIC LIVE SCAN START: assets=%s", len(universe))
        candidates = []
        for pair in universe:
            try:
                df, err = await a.get_ot_candles(pair, 60, 120)
                if df is None:
                    continue
                result = a.analyze_pair(pair, df)
                if str(result.get("signal", "NO SIGNAL")).upper() == "NO SIGNAL":
                    continue
                candidates.append(result)
            except Exception as exc:
                a.log.warning("CLASSIC TECHNICAL SCAN FAILED %s: %s", pair, exc)

        candidates.sort(key=lambda x: float(x.get("confidence", 0)), reverse=True)
        a.log.info("CLASSIC TECHNICAL CANDIDATES: %s", len(candidates))
        for result in candidates[:max(1, min(int(a.MAX_AI_CANDIDATES), 2))]:
            try:
                prompt = a.ai_prompt(result)
                ai, err = await asyncio.wait_for(asyncio.to_thread(a.call_ai, prompt), timeout=30)
            except Exception as exc:
                a.log.warning("CLASSIC AI CHECK FAILED %s: %s", result.get("pair"), exc)
                continue
            if not ai or err:
                a.log.warning("CLASSIC AI REJECTED/FAILED %s: %s", result.get("pair"), err)
                continue
            decision = str(ai.get("decision", "")).upper()
            direction = str(ai.get("direction", "")).upper()
            try:
                ai_conf = int(ai.get("confidence", 0))
                duration = int(ai.get("duration_min", 5))
            except Exception:
                continue
            technical_direction = str(result.get("signal", "")).upper()
            if (
                decision == "APPROVE"
                and direction == technical_direction
                and direction in ("UP", "DOWN")
                and ai_conf >= int(a.AI_MIN_CONFIDENCE)
                and duration in EXPIRIES
            ):
                text = _classic_signal_text(result, ai)
                if text and await a.send_to_recipients(application.bot, text):
                    a.log.info(
                        "CLASSIC QUALIFIED SIGNAL SENT: pair=%s direction=%s AI=%s technical=%s expiry=%s",
                        result.get("pair"), direction, ai_conf, result.get("confidence"), duration,
                    )
                    return

        await a.send_to_recipients(
            application.bot,
            "🚫 NO QUALIFIED SIGNAL\n\n"
            "No clean technical + AI-confirmed setup passed the safety filters this cycle.\n"
            "⏱️ Next scan: automatic 5-minute cycle."
        )

    a.scan_cycle = classic_scan
    a._CLASSIC_SIGNAL_ENGINE = True
    a.log.warning("CLASSIC PRIYANITHAN SIGNAL FORMAT + LIVE AI CONFIRMATION ACTIVE")
    return True


def _boot():
    for _ in range(1800):
        try:
            if _install():
                return
        except Exception:
            a = _app()
            if a and hasattr(a, "log"):
                a.log.exception("CLASSIC SIGNAL ENGINE INSTALL FAILED")
        time.sleep(1)


try:
    import threading
    threading.Thread(target=_boot, name="classic-signal-engine", daemon=True).start()
except Exception:
    pass

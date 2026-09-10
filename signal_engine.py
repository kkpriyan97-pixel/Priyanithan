"""Priyanithan signal engine — classic Telegram signal format.

Uses the existing technical analyzer/Candice AI decision path and restores the
original compact signal card requested by the user. AUTO TRADE remains OFF.
"""
import asyncio
import os
import re
import sys
import time


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _classic_signal_text(result):
    direction = str(result.get("signal", "NO SIGNAL")).upper()
    if direction not in ("UP", "DOWN"):
        return None
    arrow = "⬆️" if direction == "UP" else "⬇️"
    expiry = result.get("duration") or result.get("expiry") or 5
    try:
        expiry = int(expiry)
    except Exception:
        expiry = 5
    confidence = result.get("confidence", 0)
    technical = result.get("technical_confidence", result.get("confidence", 0))
    candice = result.get("ai_decision", "APPROVED")
    return (
        "🔥 PRIYANITHAN AI SIGNAL 🔥\n\n"
        f"📈 {result['pair']}\n\n"
        f"{arrow} {direction}\n\n"
        f"💰 Entry: {result.get('price', result.get('entry', 'LIVE'))}\n\n"
        f"⏱️ Expiry: {expiry} MIN\n\n"
        f"🤖 AI Confidence: {confidence}%\n\n"
        f"📊 Technical: {technical}%\n\n"
        f"🕐 {time.strftime('%H:%M:%S')} UAE\n\n"
        f"🧠 Candice AI: {candice}\n\n"
        "⚠️ MANUAL TRADE — AUTO TRADE OFF"
    )


def _install():
    a = _app()
    if not a or getattr(a, "_CLASSIC_SIGNAL_ENGINE", False):
        return bool(a)
    original_scan = getattr(a, "scan_cycle", None)
    if original_scan is None:
        return False

    async def classic_scan(application):
        universe = (
            sorted(a.discovered_assets.keys())[:a.MAX_ASSETS_PER_CYCLE]
            if a.AUTO_DISCOVER_ASSETS
            else (a.MANUAL_PAIRS[:] if a.MANUAL_PAIRS else a.PAIRS[:a.MAX_ASSETS_PER_CYCLE])
        )
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
                a.log.warning("CLASSIC SCAN FAILED %s: %s", pair, exc)

        candidates.sort(key=lambda x: float(x.get("confidence", 0)), reverse=True)
        for result in candidates[:2]:
            text = _classic_signal_text(result)
            if not text:
                continue
            sent = await a.send_to_recipients(application.bot, text)
            if sent:
                return

        await a.send_to_recipients(
            application.bot,
            "🚫 NO QUALIFIED SIGNAL\n\n"
            "Candice AI did not approve a clean setup.\n"
            "⏱️ Next scan: automatic 5-minute cycle."
        )

    a.scan_cycle = classic_scan
    a._CLASSIC_SIGNAL_ENGINE = True
    a.log.warning("CLASSIC PRIYANITHAN SIGNAL FORMAT ACTIVE")
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

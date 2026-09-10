"""Final 1-minute candle analysis mode.

Runs the selected-asset scanner after each 1-minute candle boundary and keeps
AI expiry choices at 2/3/5/15 minutes. Read-only signal generation only:
this patch never submits broker orders.
"""
import asyncio
import sys
import threading
import time

EXPIRIES=(2,3,5,15)


def _app():
    m=sys.modules.get("__main__")
    if m is not None and getattr(m,"__file__","").endswith("app.py"): return m
    return sys.modules.get("app")


def _install():
    a=_app()
    if not a or getattr(a,"_ONE_MINUTE_CANDLE_MODE",False):
        return bool(a)
    try:
        import final_asset_selection_flow as flow
        flow.ALLOWED_DURATIONS=EXPIRIES
        import fast_manual_entry
        fast_manual_entry.EXPIRIES=EXPIRIES
        # Keep the app-level compatibility constants aligned too.
        a.ALLOWED_DURATIONS=EXPIRIES
        a.SIGNAL_ANALYSIS_TIMEFRAME=60
    except Exception as exc:
        try: a.log.warning("1M MODE dependency patch retry: %s",exc)
        except Exception: pass
        return False

    async def every_minute():
        # The caller invokes this between scan cycles. Align to the next
        # minute boundary so each pass analyses a newly closed 1-minute candle.
        now=time.time()
        delay=60-(now%60)
        await asyncio.sleep(max(1.0,delay))

    a.wait_until_next_5min_uae=every_minute
    a._ONE_MINUTE_CANDLE_MODE=True
    a._FINAL_SCAN_INTERVAL_SECONDS=60
    a.log.warning("1-MINUTE CANDLE MODE ACTIVE: scan each closed 1m candle; AI expiry=2/3/5/15")
    return True


def _boot():
    for _ in range(1800):
        try:
            if _install(): return
        except Exception: pass
        time.sleep(0.25)

threading.Thread(target=_boot,name="one-minute-candle-mode",daemon=True).start()

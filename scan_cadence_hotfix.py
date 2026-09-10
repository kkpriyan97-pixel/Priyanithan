"""Run the existing live scanner every minute instead of every five minutes.
The scanner itself decides whether a setup is strong enough to notify Telegram.
No trade execution is performed here.
"""
import asyncio
import sys
import threading
import time

def _app():
    m=sys.modules.get("__main__")
    if m is not None and getattr(m,"__file__","").endswith("app.py"):
        return m
    return sys.modules.get("app")

def _install():
    a=_app()
    if not a or getattr(a,"_ONE_MINUTE_SCAN_CADENCE",False):
        return bool(a)
    async def every_minute():
        await asyncio.sleep(60)
    if not callable(getattr(a,"wait_until_next_5min_uae",None)):
        return False
    a.wait_until_next_5min_uae=every_minute
    a._ONE_MINUTE_SCAN_CADENCE=True
    a.log.warning("FINAL SCAN CADENCE ACTIVE: opportunity scan every ~60 seconds")
    return True

def _boot():
    for _ in range(1800):
        try:
            if _install():
                return
        except Exception:
            pass
        time.sleep(.1)

threading.Thread(target=_boot,name="one-minute-scan-cadence",daemon=True).start()

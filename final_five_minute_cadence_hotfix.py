"""Final cadence: selected-asset signals are evaluated every 5 minutes."""
import asyncio
import sys
import threading
import time
from datetime import timedelta


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


async def _next_5m():
    a = _app()
    if a is None:
        await asyncio.sleep(300)
        return
    now = a.now_uae()
    next_minute = ((now.minute // 5) + 1) * 5
    target = ((now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
              if next_minute >= 60 else now.replace(minute=next_minute, second=0, microsecond=0))
    await asyncio.sleep(max(1, (target - now).total_seconds()))


def _boot():
    for _ in range(1800):
        try:
            a = _app()
            if a is not None and hasattr(a, "now_uae"):
                a.wait_until_next_5min_uae = _next_5m
                a._FINAL_FIVE_MINUTE_CADENCE = True
                if hasattr(a, "log"):
                    a.log.warning("FINAL CADENCE ACTIVE: selected asset scan every 5 minutes")
                return
        except Exception:
            pass
        time.sleep(0.25)

threading.Thread(target=_boot, name="final-five-minute-cadence", daemon=True).start()

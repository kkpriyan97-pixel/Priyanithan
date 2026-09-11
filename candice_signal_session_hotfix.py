"""Candice two-hour signal-session gate.

Keeps market research available continuously while restricting signal creation
to an explicit two-hour window. The window is configured with environment
variables and defaults to disabled until an administrator sets it, preventing
an accidental transition into unrestricted signal generation.

This layer only gates the native selected-asset scan. It never places broker
orders, logs in to a broker, changes credentials, enables auto-trade, or
weakens the existing AI/technical approval gates.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timezone

PATCHED = False


def _app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    return sys.modules.get("app")


def _minutes(value, default=None):
    try:
        hour, minute = str(value).strip().split(":", 1)
        hour, minute = int(hour), int(minute)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute
    except Exception:
        pass
    return default


def session_config():
    start = _minutes(os.getenv("CANDICE_SIGNAL_SESSION_START", ""), None)
    end = _minutes(os.getenv("CANDICE_SIGNAL_SESSION_END", ""), None)
    if start is None or end is None or start == end:
        return {"enabled": False, "start": start, "end": end}
    return {"enabled": True, "start": start, "end": end}


def signal_session_active(now=None):
    cfg = session_config()
    if not cfg["enabled"]:
        return False
    if now is None:
        now = time.time()
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    minute = dt.hour * 60 + dt.minute
    start, end = cfg["start"], cfg["end"]
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def session_status(now=None):
    cfg = session_config()
    return {
        "enabled": cfg["enabled"],
        "active": signal_session_active(now),
        "start_utc_minute": cfg["start"],
        "end_utc_minute": cfg["end"],
    }


async def _scan_cycle_guarded(application):
    module = _app()
    if module is None:
        return
    if not signal_session_active():
        module.log.info("CANDICE SIGNAL SESSION CLOSED: research continues; signal scan skipped")
        return
    original = getattr(module, "_CANDICE_SESSION_ORIGINAL_SCAN_CYCLE", None)
    if not callable(original):
        module.log.warning("CANDICE SIGNAL SESSION: original scan_cycle unavailable")
        return
    await original(application)


def _patch(module):
    global PATCHED
    if getattr(module, "_CANDICE_SIGNAL_SESSION_V1", False):
        PATCHED = True
        return True
    scan = getattr(module, "scan_cycle", None)
    if not callable(scan):
        return False
    module._CANDICE_SESSION_ORIGINAL_SCAN_CYCLE = scan
    module.scan_cycle = _scan_cycle_guarded
    module.candice_signal_session_active = signal_session_active
    module.candice_signal_session_status = session_status
    module._CANDICE_SIGNAL_SESSION_V1 = True
    module.log.warning(
        "CANDICE SIGNAL SESSION V1 ACTIVE: two-hour gate; closed session = research only"
    )
    PATCHED = True
    return True


def _boot():
    for _ in range(1800):
        try:
            module = _app()
            if module is not None and _patch(module):
                return
        except Exception:
            pass
        time.sleep(0.1)

threading.Thread(target=_boot, name="candice-signal-session-boot", daemon=True).start()

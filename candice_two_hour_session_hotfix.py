"""Candice recurring two-hour signal-session gate.

Signal analysis is permitted only inside the current two-hour wall-clock
session. Outside a session, the separate 24/7 research brain remains free to
observe and learn. The default schedule is aligned to UTC even hours and can
be changed with CANDICE_SESSION_START_UTC_HOUR. This gate never places orders,
changes credentials, lowers AI gates, or forces a signal.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from datetime import datetime, timezone

PATCHED = False
SESSION_HOURS = 2
DEFAULT_START_HOUR = 0


def _app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    return sys.modules.get("app")


def _start_hour():
    try:
        return int(os.getenv("CANDICE_SESSION_START_UTC_HOUR", DEFAULT_START_HOUR)) % 24
    except Exception:
        return DEFAULT_START_HOUR


def session_state(ts=None):
    ts = time.time() if ts is None else float(ts)
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    start_hour = _start_hour()
    elapsed_hours = (dt.hour - start_hour) % 24
    active = elapsed_hours < SESSION_HOURS
    start = dt.replace(hour=(start_hour + (dt.hour - start_hour) // 2 * 2) % 24,
                      minute=0, second=0, microsecond=0)
    # Simpler and unambiguous: derive the current two-hour slot from the
    # elapsed hour offset from the configured start hour.
    slot_index = elapsed_hours // SESSION_HOURS
    start_hour_actual = (start_hour + slot_index * SESSION_HOURS) % 24
    day_shift = 0
    if start_hour_actual > dt.hour:
        day_shift = -1
    start_dt = dt.replace(hour=start_hour_actual, minute=0, second=0, microsecond=0)
    if day_shift < 0:
        start_dt = start_dt.replace(day=dt.day) - __import__('datetime').timedelta(days=1)
    end_dt = start_dt + __import__('datetime').timedelta(hours=SESSION_HOURS)
    remaining = max(0.0, end_dt.timestamp() - ts) if active else max(0.0, (start_dt.timestamp() + SESSION_HOURS * 3600) - ts)
    return {
        "active": active,
        "mode": "SIGNAL_SESSION" if active else "RESEARCH_ONLY",
        "start_ts": start_dt.timestamp(),
        "end_ts": end_dt.timestamp(),
        "remaining_seconds": remaining,
        "start_utc": start_dt.isoformat(),
        "end_utc": end_dt.isoformat(),
    }


def signal_session_active(ts=None):
    return bool(session_state(ts)["active"])


def _patch(module):
    global PATCHED
    if getattr(module, "_CANDICE_TWO_HOUR_SESSION_V1", False):
        PATCHED = True
        return True
    scan = getattr(module, "scan_cycle", None)
    if not callable(scan):
        return False
    module._CANDICE_SESSION_ORIGINAL_SCAN_CYCLE = scan

    async def gated_scan(application):
        state = session_state()
        module.candice_session_state = state
        if not state["active"]:
            module.log.info(
                "CANDICE SESSION GATE: RESEARCH_ONLY start=%s end=%s",
                state["start_utc"], state["end_utc"],
            )
            return
        module.log.info(
            "CANDICE SESSION GATE: SIGNAL_SESSION remaining=%ss end=%s",
            int(state["remaining_seconds"]), state["end_utc"],
        )
        return await module._CANDICE_SESSION_ORIGINAL_SCAN_CYCLE(application)

    module.scan_cycle = gated_scan
    module.signal_session_active = signal_session_active
    module.candice_session_state = session_state()
    module._CANDICE_TWO_HOUR_SESSION_V1 = True
    module.log.warning(
        "CANDICE 2-HOUR SESSION V1 ACTIVE: signal windows aligned to UTC even hours; research remains 24/7"
    )
    PATCHED = True
    return True


def _boot():
    for _ in range(1800):
        try:
            module = _app()
            if module is not None and callable(getattr(module, "scan_cycle", None)):
                if _patch(module):
                    return
        except Exception:
            pass
        time.sleep(0.2)

threading.Thread(target=_boot, name="candice-two-hour-session", daemon=True).start()

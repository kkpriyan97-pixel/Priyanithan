"""Candice recurring two-hour signal-session gate.

Candice operates in repeating two-hour wall-clock slots. During each active
slot, the existing selected-asset signal scan is allowed to run. Outside the
slot, signal scans are blocked while the separate 24/7 research/learning brain
continues. This layer never places broker orders, changes credentials, lowers
AI gates, or forces a signal.
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PATCHED = False
SESSION_HOURS = 2
UAE_TZ = ZoneInfo("Asia/Dubai")


def _app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    return sys.modules.get("app")


def session_state(ts=None):
    ts = time.time() if ts is None else float(ts)
    now_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    slot_hour = (now_utc.hour // SESSION_HOURS) * SESSION_HOURS
    start_utc = now_utc.replace(hour=slot_hour, minute=0, second=0, microsecond=0)
    end_utc = start_utc + timedelta(hours=SESSION_HOURS)
    active = start_utc <= now_utc < end_utc
    boundary = end_utc if active else start_utc
    remaining = max(0.0, boundary.timestamp() - ts)
    return {
        "active": active,
        "mode": "SIGNAL_SESSION" if active else "RESEARCH_ONLY",
        "start_ts": start_utc.timestamp(),
        "end_ts": end_utc.timestamp(),
        "remaining_seconds": remaining,
        "start_utc": start_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "end_utc": end_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "start_uae": start_utc.astimezone(UAE_TZ).strftime("%Y-%m-%d %H:%M:%S UAE"),
        "end_uae": end_utc.astimezone(UAE_TZ).strftime("%Y-%m-%d %H:%M:%S UAE"),
    }


def signal_session_active(ts=None):
    return bool(session_state(ts)["active"])


def _patch(module):
    global PATCHED
    if getattr(module, "_CANDICE_SIGNAL_SESSION_V2", False):
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
                "CANDICE SESSION GATE: RESEARCH_ONLY next=%s remaining=%ss",
                state["start_utc"], int(state["remaining_seconds"]),
            )
            return
        module.log.info(
            "CANDICE SESSION GATE: SIGNAL_SESSION end=%s remaining=%ss",
            state["end_utc"], int(state["remaining_seconds"]),
        )
        return await module._CANDICE_SESSION_ORIGINAL_SCAN_CYCLE(application)

    module.scan_cycle = gated_scan
    module.signal_session_active = signal_session_active
    module.session_state = session_state
    module.candice_session_state = session_state()
    module._CANDICE_SIGNAL_SESSION_V2 = True
    module.log.warning(
        "CANDICE 2-HOUR SESSION V2 ACTIVE: repeating 2h signal/research cycle"
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
        time.sleep(0.2)

threading.Thread(target=_boot, name="candice-signal-session-v2", daemon=True).start()

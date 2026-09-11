"""Candice recurring 2-hour signal / 2-hour research cycle.

Candice alternates between a two-hour SIGNAL_SESSION and a two-hour
RESEARCH_ONLY period. The cycle repeats continuously. Signal generation is
blocked at the final analysis boundary as well as at the selected scan
boundary, so the legacy/native 5-minute scan cannot bypass the session gate.
The independent 24/7 research brain remains unaffected.

This layer never places broker orders, uses broker credentials, enables
auto-trading, enables martingale, forces signals, or weakens AI gates.
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PATCHED = False
SIGNAL_HOURS = 2
RESEARCH_HOURS = 2
CYCLE_HOURS = SIGNAL_HOURS + RESEARCH_HOURS
UAE_TZ = ZoneInfo("Asia/Dubai")


def _app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    return sys.modules.get("app")


def session_state(ts=None):
    ts = time.time() if ts is None else float(ts)
    now_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    cycle_hour = (now_utc.hour // CYCLE_HOURS) * CYCLE_HOURS
    cycle_start = now_utc.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)
    signal_end = cycle_start + timedelta(hours=SIGNAL_HOURS)
    cycle_end = cycle_start + timedelta(hours=CYCLE_HOURS)
    active = cycle_start <= now_utc < signal_end
    boundary = signal_end if active else cycle_end
    next_signal = cycle_start if active else cycle_end
    remaining = max(0.0, boundary.timestamp() - ts)
    return {
        "active": active,
        "mode": "SIGNAL_SESSION" if active else "RESEARCH_ONLY",
        "start_ts": cycle_start.timestamp(),
        "end_ts": boundary.timestamp(),
        "remaining_seconds": remaining,
        "start_utc": cycle_start.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "end_utc": boundary.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "start_uae": cycle_start.astimezone(UAE_TZ).strftime("%Y-%m-%d %H:%M:%S UAE"),
        "end_uae": boundary.astimezone(UAE_TZ).strftime("%Y-%m-%d %H:%M:%S UAE"),
        "next_signal_ts": next_signal.timestamp(),
        "next_signal_utc": next_signal.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "next_signal_uae": next_signal.astimezone(UAE_TZ).strftime("%Y-%m-%d %H:%M:%S UAE"),
    }


def signal_session_active(ts=None):
    return bool(session_state(ts)["active"])


def _patch(module):
    global PATCHED
    if getattr(module, "_CANDICE_SIGNAL_SESSION_V4", False):
        PATCHED = True
        return True
    scan = getattr(module, "scan_cycle", None)
    analyze = getattr(module, "analyze_asset", None)
    if not callable(scan) or not callable(analyze):
        return False

    module._CANDICE_SESSION_ORIGINAL_SCAN_CYCLE = scan
    module._CANDICE_SESSION_ORIGINAL_ANALYZE_ASSET = analyze

    async def gated_analyze(pair):
        state = session_state()
        module.candice_session_state = state
        if not state["active"]:
            module.log.info(
                "CANDICE ANALYSIS BLOCKED: RESEARCH_ONLY pair=%s next_signal=%s",
                pair, state["next_signal_utc"],
            )
            return None, "Candice signal session is closed; research mode only"
        return await module._CANDICE_SESSION_ORIGINAL_ANALYZE_ASSET(pair)

    async def gated_scan(application):
        state = session_state()
        module.candice_session_state = state
        if not state["active"]:
            module.log.info(
                "CANDICE SESSION GATE: RESEARCH_ONLY next_signal=%s remaining=%ss",
                state["next_signal_utc"], int(state["remaining_seconds"]),
            )
            return
        module.log.info(
            "CANDICE SESSION GATE: SIGNAL_SESSION end=%s remaining=%ss",
            state["end_utc"], int(state["remaining_seconds"]),
        )
        return await module._CANDICE_SESSION_ORIGINAL_SCAN_CYCLE(application)

    module.analyze_asset = gated_analyze
    module.scan_cycle = gated_scan
    module.signal_session_active = signal_session_active
    module.session_state = session_state
    module.candice_session_state = session_state()
    module._CANDICE_SIGNAL_SESSION_V4 = True
    module.log.warning(
        "CANDICE 2H/2H SESSION V4 ACTIVE: ALL SIGNAL PATHS GATED; research remains 24/7"
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

threading.Thread(target=_boot, name="candice-signal-session-v4", daemon=True).start()

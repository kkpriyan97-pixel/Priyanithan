"""Reliable selected-asset 5-minute boundary scan bridge.

The UI countdown expects app.scan_cycle(), but the native app previously only
exposed scan_loop().  This module adds the missing native entry point and
keeps one analysis attempt per pair/boundary.  It never places broker orders.
"""
import asyncio
import sys
import threading
import time

PATCHED = False


def _app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    return sys.modules.get("app")


async def _scan_cycle(module, application):
    now = time.time()
    boundary = int(now // 300) * 300
    for uid, pair in list(getattr(module, "selected_asset", {}).items()):
        if uid in getattr(module, "active_signal", {}):
            continue
        key = (int(uid), str(pair).upper(), boundary)
        seen = getattr(module, "_SELECTED_SCAN_BOUNDARIES", None)
        if seen is None:
            seen = set()
            module._SELECTED_SCAN_BOUNDARIES = seen
        if key in seen:
            continue
        seen.add(key)
        try:
            signal, err = await module.analyze_asset(pair)
            if signal is None:
                module.log.info(
                    "SELECTED BOUNDARY SCAN NO SIGNAL pair=%s uid=%s boundary=%s reason=%s",
                    pair, uid, module.fmt_ts(boundary), err,
                )
                continue
            module.active_signal[uid] = signal
            await module.send_signal(application.bot, uid, signal)
            asyncio.create_task(
                module.monitor_result(application.bot, uid, signal),
                name=f"result-{pair}-{uid}",
            )
            module.log.warning(
                "SELECTED BOUNDARY SIGNAL SENT pair=%s uid=%s boundary=%s duration=%s",
                pair, uid, module.fmt_ts(boundary), signal.get("duration"),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            module.log.exception(
                "SELECTED BOUNDARY SCAN FAILED pair=%s uid=%s boundary=%s: %s",
                pair, uid, module.fmt_ts(boundary), exc,
            )


async def scan_cycle(application):
    module = _app()
    if module is None:
        return
    await _scan_cycle(module, application)


def _patch(module):
    global PATCHED
    if getattr(module, "_SELECTED_SCAN_CYCLE_V1", False):
        PATCHED = True
        return True
    module.scan_cycle = scan_cycle
    module._SELECTED_SCAN_CYCLE_V1 = True
    module.log.warning(
        "SELECTED SCAN CYCLE V1 ACTIVE: 5m boundary -> native analyze/send/result"
    )
    PATCHED = True
    return True


def _boot():
    for _ in range(1800):
        try:
            module = _app()
            if module is not None and callable(getattr(module, "analyze_asset", None)):
                _patch(module)
                return
        except Exception:
            pass
        time.sleep(0.1)

threading.Thread(target=_boot, name="selected-scan-cycle-boot", daemon=True).start()

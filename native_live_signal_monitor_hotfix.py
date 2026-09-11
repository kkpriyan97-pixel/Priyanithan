"""Bridge the native app.send_signal path into the live read-only monitor.

The signal engine calls app.send_signal directly. Older live-monitor patches
hooked send_to_recipients(), so native signals could reach Telegram without
starting the 30-second live monitor. This bridge hooks the final send_signal
function after the existing UI/countdown patches and starts the read-only
monitor for the exact same signal state.

No broker order is created, modified, or closed here.
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


async def _start_live_monitor(module, bot, uid, signal):
    """Start the existing live AI monitor with a normalized native signal."""
    monitor = sys.modules.get("live_ai_monitor_hotfix")
    if monitor is None:
        try:
            import live_ai_monitor_hotfix as monitor
        except Exception as exc:
            module.log.warning("NATIVE LIVE MONITOR: import failed: %s", exc)
            return

    fn = getattr(monitor, "_live_monitor", None)
    if not callable(fn):
        module.log.warning("NATIVE LIVE MONITOR: _live_monitor unavailable")
        return

    try:
        duration = int(signal.get("duration", 5))
    except (TypeError, ValueError):
        duration = 5
    duration = duration if duration in (2, 3, 5, 10, 15) else 5

    normalized = {
        "_bot": bot,
        "uid": int(uid),
        "pair": str(signal.get("pair", "")).upper(),
        "direction": str(signal.get("direction", "UP")).upper(),
        "entry": float(signal.get("price", 0.0)),
        "signal_time": float(signal.get("created_at", time.time())),
        "expiry_min": duration,
        "status": "PENDING",
    }

    try:
        await fn(module, normalized)
    except asyncio.CancelledError:
        raise
    except Exception:
        module.log.exception("NATIVE LIVE MONITOR FAILED: %s", normalized["pair"])


async def _patched_send_signal(bot, uid, signal):
    module = _app()
    if module is None:
        return

    original = getattr(module, "_NATIVE_LIVE_ORIGINAL_SEND_SIGNAL", None)
    if not callable(original):
        module.log.warning("NATIVE LIVE MONITOR: original send_signal missing")
        return

    # Preserve the existing signal delivery/countdown implementation first.
    result = await original(bot, uid, signal)

    # The live monitor is read-only and independent from the exact expiry
    # result monitor already created by the signal engine.
    tasks = getattr(module, "_NATIVE_LIVE_MONITOR_TASKS", None)
    if tasks is None:
        tasks = set()
        module._NATIVE_LIVE_MONITOR_TASKS = tasks

    task = asyncio.create_task(
        _start_live_monitor(module, bot, uid, signal),
        name=f"native-live-monitor-{signal.get('pair', 'asset')}-{uid}",
    )
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    module.log.info(
        "NATIVE LIVE SIGNAL MONITOR ACTIVE: %s %s %sM uid=%s",
        signal.get("pair"), signal.get("direction"), signal.get("duration"), uid,
    )
    return result


def _patch(module):
    global PATCHED
    if getattr(module, "_NATIVE_LIVE_MONITOR_V1", False):
        PATCHED = True
        return True
    current = getattr(module, "send_signal", None)
    if not callable(current):
        return False

    module._NATIVE_LIVE_ORIGINAL_SEND_SIGNAL = current
    module.send_signal = _patched_send_signal
    module._NATIVE_LIVE_MONITOR_V1 = True
    PATCHED = True
    module.log.warning(
        "NATIVE LIVE SIGNAL MONITOR V1 ACTIVE: native send_signal -> 30s live AI monitor"
    )
    return True


def _boot():
    # Startup hotfix modules race the app import; wait for send_signal to exist.
    for _ in range(1800):
        try:
            module = _app()
            if module is not None and _patch(module):
                return
        except Exception:
            pass
        time.sleep(0.1)


threading.Thread(target=_boot, name="native-live-monitor-boot", daemon=True).start()

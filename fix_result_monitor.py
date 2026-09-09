from pathlib import Path

# Permanent result-monitor repair.
# 1) Keep sitecustomize.py free of duplicate send wrappers.
# 2) Make trade_result_monitor.py restart a missing/dead expiry task instead
#    of treating a stale PENDING record as already monitored.
site = Path("sitecustomize.py")
s = site.read_text(encoding="utf-8")
marker = "# READ-ONLY SIGNAL RESULT + SEND-TIME CLOCK PATCH"
if marker not in s:
    raise SystemExit("sitecustomize.py result-monitor marker not found")

head = s.split(marker, 1)[0]
clean_tail = r'''# READ-ONLY SIGNAL RESULT + SEND-TIME CLOCK PATCH
# Result monitoring is owned exclusively by trade_result_monitor.py.
import re as _rm_re
import threading as _rm_threading
import time as _rm_time
from datetime import datetime as _rm_datetime
from zoneinfo import ZoneInfo as _rm_ZoneInfo

_RM_UAE = _rm_ZoneInfo("Asia/Dubai")
_RM_TIME_RE = _rm_re.compile(r"🕐\s*[^\n]*UAE")


def _install_result_clock_patch():
    module = sys.modules.get("__main__")
    if module is None or not getattr(module, "__file__", "").endswith("app.py"):
        module = sys.modules.get("app")
    if module is None or getattr(module, "_RESULT_CLOCK_PATCH_INSTALLED", False):
        return False
    original_format_signal = getattr(module, "format_signal", None)
    if original_format_signal is None:
        return False

    def patched_format_signal(result, ai):
        text = original_format_signal(result, ai)
        now_text = _rm_datetime.now(_RM_UAE).strftime("%H:%M:%S UAE")
        return _RM_TIME_RE.sub("🕐 " + now_text, text, count=1)

    module.format_signal = patched_format_signal
    module._RESULT_CLOCK_PATCH_INSTALLED = True
    module.log.info("RESULT CLOCK PATCH INSTALLED; outcome monitor delegated to trade_result_monitor.py")
    return True


def _bootstrap_result_clock():
    for _ in range(1800):
        if _install_result_clock_patch():
            return
        _rm_time.sleep(0.1)


def _bootstrap_result_monitor_module():
    # Import the sole result-monitor owner after app.py is available.
    for _ in range(1800):
        try:
            import trade_result_monitor  # noqa: F401
            module = sys.modules.get("__main__")
            if module is None or not getattr(module, "__file__", "").endswith("app.py"):
                module = sys.modules.get("app")
            if module is not None and getattr(module, "_RESULT_MONITOR_INSTALLED", False):
                return
        except Exception:
            module = sys.modules.get("__main__") or sys.modules.get("app")
            if module is not None and hasattr(module, "log"):
                module.log.exception("RESULT MONITOR BOOTSTRAP IMPORT FAILED")
        _rm_time.sleep(0.1)


_rm_threading.Thread(target=_bootstrap_result_clock, name="signal-clock-bootstrap", daemon=True).start()
_rm_threading.Thread(target=_bootstrap_result_monitor_module, name="signal-result-monitor-bootstrap", daemon=True).start()
'''
s = head + clean_tail
compile(s, "sitecustomize.py", "exec")
site.write_text(s, encoding="utf-8")

rm = Path("trade_result_monitor.py")
r = rm.read_text(encoding="utf-8")
old = '''                module.log.info("SIGNAL RESULT MONITOR ALREADY REGISTERED: pair=%s direction=%s", pair, direction)\n                return False\n'''
new = '''                existing_task = signal.get("_monitor_task")\n                if existing_task is not None and not existing_task.done():\n                    module.log.info("SIGNAL RESULT MONITOR ALREADY REGISTERED: pair=%s direction=%s", pair, direction)\n                    return False\n                module.log.warning("STALE PENDING MONITOR RECOVERED: pair=%s direction=%s", pair, direction)\n                break\n'''
if old not in r:
    raise SystemExit("expected pending-monitor guard not found")
r = r.replace(old, new, 1)

old2 = '''    tasks.add(task)\n    task.add_done_callback(tasks.discard)\n    module.log.info("SIGNAL RESULT MONITOR STARTED (DIRECT): pair=%s direction=%s expiry=%s min signal_time=%s", pair, direction, expiry, datetime.now(_UAE).strftime("%H:%M:%S UAE"))\n'''
new2 = '''    signal["_monitor_task"] = task\n    tasks.add(task)\n    task.add_done_callback(tasks.discard)\n    module.log.info("SIGNAL RESULT MONITOR STARTED (DIRECT): pair=%s direction=%s expiry=%s min signal_time=%s task=%s", pair, direction, expiry, datetime.now(_UAE).strftime("%H:%M:%S UAE"), getattr(task, "get_name", lambda: "task")())\n'''
if old2 not in r:
    raise SystemExit("expected direct task registration block not found")
r = r.replace(old2, new2, 1)

old3 = '''            module.pending_signal_tasks.add(task)\n            task.add_done_callback(module.pending_signal_tasks.discard)\n            module.log.info("SIGNAL RESULT MONITOR STARTED: pair=%s direction=%s expiry=%s min signal_time=%s task=%s", pair, direction, expiry_text, datetime.now(_UAE).strftime("%H:%M:%S UAE"), getattr(task, "get_name", lambda: "task")())\n'''
new3 = '''            signal["_monitor_task"] = task\n            module.pending_signal_tasks.add(task)\n            task.add_done_callback(module.pending_signal_tasks.discard)\n            module.log.info("SIGNAL RESULT MONITOR STARTED: pair=%s direction=%s expiry=%s min signal_time=%s task=%s", pair, direction, expiry_text, datetime.now(_UAE).strftime("%H:%M:%S UAE"), getattr(task, "get_name", lambda: "task")())\n'''
if old3 not in r:
    raise SystemExit("expected wrapped task registration block not found")
r = r.replace(old3, new3, 1)
compile(r, "trade_result_monitor.py", "exec")
rm.write_text(r, encoding="utf-8")
print("FIXED: result monitor now recovers stale PENDING records when their task is missing/dead")
print("FIXED: every signal stores its live monitor task")
print("OK: sitecustomize.py and trade_result_monitor.py compile")

# CI trigger marker.
Path("fix_result_monitor.py").touch()

from pathlib import Path

p = Path("sitecustomize.py")
s = p.read_text(encoding="utf-8")

# Permanent architecture fix:
# trade_result_monitor.py is the single owner of signal registration, expiry
# tasks, and WIN/LOSS delivery. sitecustomize.py must not install a second
# result-monitor wrapper. The old duplicate wrapper caused REGISTER FAILED.
marker = "# READ-ONLY SIGNAL RESULT + SEND-TIME CLOCK PATCH"
if marker not in s:
    raise SystemExit("sitecustomize.py result-monitor marker not found")

head = s.split(marker, 1)[0]
clean_tail = r'''# READ-ONLY SIGNAL RESULT + SEND-TIME CLOCK PATCH
# Result monitoring itself is owned exclusively by trade_result_monitor.py.
# This layer only keeps the Telegram signal timestamp accurate.
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
    # Importing trade_result_monitor starts its own fail-safe bootstrap thread.
    # Do this from sitecustomize only after the runtime has started so the
    # monitor module is guaranteed to be loaded in the Render process.
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
p.write_text(s, encoding="utf-8")
compile(s, "sitecustomize.py", "exec")
print("FIXED: duplicate sitecustomize result monitor removed and sole trade_result_monitor bootstrap restored")
print("OK: sitecustomize.py compiles")

"""Small runtime compatibility fixes loaded without changing the broker/trade path."""
import re
import sys
import threading
import time


_NEW_SIGNAL_RE = re.compile(
    r"(?:🔥\s*)?PRIYANITHAN(?: AI)? SIGNAL(?:\s*🔥)?.*?"
    r"📈\s*(?:Asset\s*:\s*)?([A-Z0-9_]+).*?"
    r"(?:⬆️\s*|⬇️\s*|Direction\s*:\s*(?:BUY / |SELL / )?)(UP|DOWN).*?"
    r"💰\s*Entry\s*:\s*([0-9.]+).*?"
    r"(?:⏱️\s*(?:Expiry|Duration)\s*:\s*)(1|2|3|5|10|15)\s*MIN",
    re.S | re.I,
)


def _patch():
    mod = sys.modules.get("trade_result_monitor")
    if mod is None:
        return False
    mod._SIGNAL_RE = _NEW_SIGNAL_RE
    return True


def _boot():
    for _ in range(1800):
        try:
            if _patch():
                return
        except Exception:
            pass
        time.sleep(1)

threading.Thread(target=_boot, name="runtime-fixes", daemon=True).start()

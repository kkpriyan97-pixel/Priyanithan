"""Final policy: no Trade Now/deep-link UI is attached to signals."""
import sys
import threading
import time


def _boot():
    for _ in range(1800):
        try:
            import fast_manual_entry
            fast_manual_entry.signal_markup = lambda cid, text: None
            a = sys.modules.get("__main__") or sys.modules.get("app")
            if a is not None and hasattr(a, "log"):
                a.log.warning("FINAL POLICY ACTIVE: TRADE NOW UI DISABLED")
            return
        except Exception:
            time.sleep(0.25)

threading.Thread(target=_boot, name="disable-trade-now", daemon=True).start()

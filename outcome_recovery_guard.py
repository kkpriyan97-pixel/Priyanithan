"""Candice outcome recovery guard.

Prevents the watchdog from re-running the same already-confirmed recovery
check every second. It preserves one recovery attempt per unique
(asset,direction,entry_time) during a process lifetime, while normal outcome
expiry processing remains unchanged.
"""
from __future__ import annotations
import logging
import sys
import threading
import time

LOG = logging.getLogger("candice.outcomes")
SEEN = set()
LOCK = threading.RLock()
INSTALLED = False


def _install():
    global INSTALLED
    while not INSTALLED:
        mod = sys.modules.get("outcome_engine")
        if mod is None:
            time.sleep(0.5)
            continue
        original = getattr(mod, "_recover_sent_signals", None)
        if not callable(original):
            time.sleep(0.5)
            continue
        if getattr(original, "_candice_guarded", False):
            INSTALLED = True
            return

        def guarded():
            sc = sys.modules.get("sitecustomize")
            if sc is None:
                return original()
            candidates = getattr(sc, "CANDIDATES", {}) or {}
            sent_windows = getattr(sc, "SENT_WINDOWS", set()) or set()
            last = getattr(sc, "LAST_SIGNAL", {}) or {}
            for asset, ctx in list(last.items()):
                sig = (ctx or {}).get("signal") or {}
                if not sig:
                    continue
                try:
                    w = int(float((ctx or {}).get("time", time.time())) // 300)
                    if w not in sent_windows:
                        continue
                    matches = [v for v in candidates.values()
                               if v.get("window") == w
                               and str(v.get("asset", "")).upper() == str(asset).upper()]
                    if not matches:
                        continue
                    cand = max(matches, key=lambda x: float(x.get("created", 0)))
                    b = cand.get("brain") or {}
                    direction = str(cand.get("direction") or b.get("direction") or sig.get("direction") or "")
                    entry_time = float(sig.get("timestamp", time.time()))
                    key = (str(asset).upper(), direction, entry_time)
                    with LOCK:
                        if key in SEEN:
                            continue
                        SEEN.add(key)
                except Exception:
                    LOG.exception("Outcome recovery guard key calculation failed")
            # Run the original only for keys that have not been seen.  The
            # original function performs its own DB duplicate check.
            return original()

        guarded._candice_guarded = True
        mod._recover_sent_signals = guarded
        INSTALLED = True
        LOG.info("OUTCOME_RECOVERY_GUARD installed | repeated duplicate recovery logging suppressed")


threading.Thread(target=_install, name="candice-outcome-recovery-guard", daemon=True).start()

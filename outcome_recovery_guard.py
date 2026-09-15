"""Candice outcome recovery guard.

Limits the recovery helper to one pass per unique delivered-signal key during
a process lifetime. Normal pending-signal expiry processing is untouched.
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


def _keys():
    sc = sys.modules.get("sitecustomize")
    if sc is None:
        return set()
    candidates = getattr(sc, "CANDIDATES", {}) or {}
    sent_windows = getattr(sc, "SENT_WINDOWS", set()) or set()
    last = getattr(sc, "LAST_SIGNAL", {}) or {}
    keys = set()
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
            keys.add((str(asset).upper(), direction, entry_time))
        except Exception:
            LOG.exception("Outcome recovery guard key calculation failed")
    return keys


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
            keys = _keys()
            with LOCK:
                new_keys = keys - SEEN
            if not new_keys:
                return
            result = original()
            with LOCK:
                SEEN.update(keys)
            LOG.info("OUTCOME_RECOVERY_PASS completed | new_keys=%s | repeated checks suppressed", len(new_keys))
            return result

        guarded._candice_guarded = True
        mod._recover_sent_signals = guarded
        INSTALLED = True
        LOG.info("OUTCOME_RECOVERY_GUARD installed | repeated duplicate recovery logging suppressed")


threading.Thread(target=_install, name="candice-outcome-recovery-guard", daemon=True).start()

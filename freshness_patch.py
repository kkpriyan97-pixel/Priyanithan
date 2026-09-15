"""Runtime freshness bridge for real completed 1m candles."""
from __future__ import annotations
import logging
import sys
import threading
import time

LOG = logging.getLogger("candice.freshness")


def _ts(value):
    try:
        x = float(value or 0)
        if x > 10_000_000_000:
            x /= 1000.0
        return x if x > 0 else 0.0
    except Exception:
        return 0.0


def _age(candle, asset=None):
    now = time.time()
    sc = sys.modules.get("sitecustomize")
    if sc is not None and asset:
        try:
            with getattr(sc, "LOCK"):
                rec = (getattr(sc, "LIVE_RECEIPTS", {}) or {}).get(asset)
            if rec and float(rec[1]) <= now + 5:
                return max(0.0, now - float(rec[1]))
        except Exception:
            pass
    try:
        received = float((candle or {}).get("received_at", 0) or 0)
        if 0 < received <= now + 5:
            return max(0.0, now - received)
    except Exception:
        pass
    ts = _ts((candle or {}).get("timestamp", (candle or {}).get("time", 0)))
    if ts > 0:
        return max(0.0, now - ts)
    return 1e9


def install():
    sc = sys.modules.get("sitecustomize")
    if sc is None:
        return False
    # sitecustomize defines its original candle_age late during module import.
    # Do not assume the first assignment wins: keep the bridge authoritative
    # through the end of Python startup so the original definition cannot race
    # and overwrite it.
    sc.candle_age = _age
    return True


def _late_install():
    found = False
    # sitecustomize can still be executing when this .pth import starts.
    # Keep enforcing the replacement for a short startup window.
    for _ in range(80):
        try:
            sc = sys.modules.get("sitecustomize")
            if sc is not None and getattr(sc, "candle_age", None) is not _age:
                sc.candle_age = _age
                found = True
                LOG.info("CANDICE_FRESHNESS_BRIDGE installed | receipt -> received_at -> candle_timestamp fallback")
            elif sc is not None:
                found = True
        except Exception:
            LOG.exception("Freshness bridge install failed")
        time.sleep(0.1)
    if not found:
        LOG.error("CANDICE_FRESHNESS_BRIDGE failed to find sitecustomize")
    else:
        LOG.info("CANDICE_FRESHNESS_BRIDGE startup enforcement complete")


threading.Thread(target=_late_install, name="candice-freshness-bridge", daemon=True).start()

"""Candice delivery freshness fix: use valid candle timestamps when receipt metadata is absent."""
from __future__ import annotations
import logging
import sys
import time

LOG = logging.getLogger("candice.runtime")


def _normalize_ts(value):
    try:
        ts = float(value)
    except Exception:
        return None
    if ts > 1e11:
        ts /= 1000.0
    return ts if 0 < ts <= time.time() + 5 else None


def _patch():
    mod = sys.modules.get("sitecustomize")
    if mod is None:
        return False

    def candle_age(c, asset=None):
        now = time.time()
        if asset:
            try:
                with mod.LOCK:
                    rec = mod.LIVE_RECEIPTS.get(asset)
                if rec:
                    received = _normalize_ts(rec[1])
                    if received is not None:
                        return max(0.0, now - received)
            except Exception:
                pass
        if isinstance(c, dict):
            received = _normalize_ts(c.get("received_at"))
            if received is not None:
                return max(0.0, now - received)
            candle_ts = _normalize_ts(c.get("timestamp", c.get("t", c.get("time"))))
            if candle_ts is not None:
                # A completed 1m candle can legitimately be up to ~60s old.
                return max(0.0, now - candle_ts)
        return 10**9

    mod.candle_age = candle_age
    LOG.info("CANDICE_DELIVERY_FRESHNESS patched | receipt + received_at + candle timestamp fallback")
    return True

_patch()

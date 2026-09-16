"""Candice delivery freshness fix: patch after sitecustomize is loaded and trust valid live candle timestamps."""
from __future__ import annotations
import logging
import sys
import threading
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
        ages = []

        if asset:
            try:
                with mod.LOCK:
                    rec = mod.LIVE_RECEIPTS.get(asset)
                if rec:
                    received = _normalize_ts(rec[1])
                    if received is not None:
                        ages.append(max(0.0, now - received))
            except Exception:
                pass

        if isinstance(c, dict):
            received = _normalize_ts(c.get("received_at"))
            if received is not None:
                ages.append(max(0.0, now - received))

            candle_ts = _normalize_ts(c.get("timestamp", c.get("t", c.get("time"))))
            if candle_ts is not None:
                # Completed 1m candles may legitimately be tens of seconds old.
                ages.append(max(0.0, now - candle_ts))

        # Use the freshest valid live timestamp. Never treat a valid candle as
        # ancient merely because an auxiliary receipt timestamp is missing/stale.
        return min(ages) if ages else 10**9

    mod.candle_age = candle_age
    LOG.info("CANDICE_DELIVERY_FRESHNESS patched | retry-safe | receipt + received_at + candle timestamp")
    return True


def _retry_until_sitecustomize():
    for _ in range(100):
        if _patch():
            return
        time.sleep(0.2)
    LOG.error("CANDICE_DELIVERY_FRESHNESS failed to attach after sitecustomize startup")


# candice_boot.pth can import this module before sitecustomize itself exists.
# Retry asynchronously so the hook is attached after Python startup ordering.
threading.Thread(target=_retry_until_sitecustomize, name="candice-freshness-hook", daemon=True).start()

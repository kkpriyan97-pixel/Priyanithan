from __future__ import annotations

import logging
from brain import CandiceBrain

log = logging.getLogger("candice.brain_fix")

_ORIGINAL_ANALYZE = CandiceBrain.analyze


def analyze(self, asset, completed, live_price):
    try:
        if getattr(self.feed, "client", None) is None:
            return None
        if getattr(self.feed.client, "account_mode", "UNKNOWN") != "DEMO":
            return None
        status = self.feed.status()
        assets = set(status.get("assets", []))
        if asset not in assets:
            return None
        if len(completed) < 60 or live_price is None:
            return None
        result = _ORIGINAL_ANALYZE(self, asset, completed, live_price)
        if not isinstance(result, dict):
            return None
        required = ("asset", "direction", "confidence", "entry", "expiry", "rsi", "adx")
        if any(k not in result or result[k] is None for k in required):
            return None
        return result
    except Exception:
        log.exception("BRAIN_ANALYZE_FAILED asset=%s", asset)
        return None


def apply():
    CandiceBrain.analyze = analyze
    log.info("BRAIN_FIX_APPLIED safe_demogate=true")

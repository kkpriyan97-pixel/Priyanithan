from __future__ import annotations

import logging
import math
from brain import CandiceBrain

log = logging.getLogger("candice.brain_fix")
_ORIGINAL_ANALYZE = CandiceBrain.analyze


def _finite(v):
    try:
        return v is not None and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def analyze(self, asset, completed, live_price):
    try:
        client = getattr(self.feed, "client", None)
        if client is None or getattr(client, "account_mode", "UNKNOWN") != "DEMO":
            return None
        authoritative = set(getattr(self.feed, "_authoritative_flex_assets", set()))
        if asset not in authoritative:
            return None
        if len(completed) < 60 or not _finite(live_price):
            return None

        rsi = self._rsi(completed)
        adx = self._adx(completed)
        atr = self._atr(completed)
        macd = self._macd(completed)
        if not (_finite(rsi) and _finite(adx) and _finite(atr)):
            log.warning("BRAIN_INDICATORS_NOT_READY asset=%s rsi=%s adx=%s atr=%s", asset, rsi, adx, atr)
            return None
        if not isinstance(macd, (tuple, list)) or len(macd) < 2:
            log.warning("BRAIN_MACD_NOT_READY asset=%s", asset)
            return None
        if not (_finite(macd[0]) and _finite(macd[1])):
            log.warning("BRAIN_MACD_NOT_READY asset=%s", asset)
            return None

        result = _ORIGINAL_ANALYZE(self, asset, completed, live_price)
        if not isinstance(result, dict):
            return None
        for key in ("asset", "direction", "expiry"):
            if result.get(key) is None:
                return None
        for key in ("confidence", "entry", "rsi", "adx"):
            if not _finite(result.get(key)):
                return None
        if result.get("direction") not in {"UP", "DOWN"}:
            return None
        return result
    except Exception:
        log.exception("BRAIN_ANALYZE_FAILED asset=%s", asset)
        return None


def apply():
    CandiceBrain.analyze = analyze
    log.info("BRAIN_FIX_APPLIED safe_demogate=true indicator_gate=true")

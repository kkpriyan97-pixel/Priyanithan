from __future__ import annotations

import logging
import math
from brain import CandiceBrain

log = logging.getLogger("candice.brain_fix")
_ORIGINAL_ANALYZE = CandiceBrain.analyze


def _number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            return float(v) if math.isfinite(float(v)) else None
        except (TypeError, ValueError):
            return None
    if isinstance(v, str):
        try:
            x = float(v.strip())
            return x if math.isfinite(x) else None
        except (TypeError, ValueError):
            return None
    if isinstance(v, dict):
        for key in ("value", "price", "rate", "quote", "close", "open", "high", "low", "v", "q", "p"):
            if key in v:
                x = _number(v[key])
                if x is not None:
                    return x
    if isinstance(v, (list, tuple)):
        for item in reversed(v):
            x = _number(item)
            if x is not None:
                return x
    return None


def _normalize_candle(candle):
    if not isinstance(candle, dict):
        return None
    out = {}
    for key in ("open", "high", "low", "close"):
        value = _number(candle.get(key, candle.get(key[0])))
        if value is None:
            return None
        out[key] = value
    ts = _number(candle.get("timestamp", candle.get("t", candle.get("time"))))
    if ts is None:
        return None
    if ts > 1e10:
        ts /= 1000.0
    out["timestamp"] = float(ts)
    return out


def _finite(v):
    return _number(v) is not None


def analyze(self, asset, completed, live_price):
    try:
        client = getattr(self.feed, "client", None)
        if client is None or getattr(client, "account_mode", "UNKNOWN") != "DEMO":
            return None
        authoritative = set(getattr(self.feed, "_authoritative_flex_assets", set()))
        if asset not in authoritative:
            return None
        normalized = []
        for candle in completed:
            item = _normalize_candle(candle)
            if item is not None:
                normalized.append(item)
        normalized.sort(key=lambda x: x["timestamp"])
        price = _number(live_price)
        if len(normalized) < 60 or price is None:
            return None

        rsi = self._rsi([x["close"] for x in normalized])
        adx = self._adx(normalized)
        atr = self._atr(normalized)
        macd = self._macd([x["close"] for x in normalized])
        if not (_finite(rsi) and _finite(adx) and _finite(atr)):
            log.warning("BRAIN_INDICATORS_NOT_READY asset=%s rsi=%s adx=%s atr=%s", asset, rsi, adx, atr)
            return None
        if not isinstance(macd, (tuple, list)) or len(macd) < 2:
            log.warning("BRAIN_MACD_NOT_READY asset=%s", asset)
            return None
        if not (_finite(macd[0]) and _finite(macd[1])):
            log.warning("BRAIN_MACD_NOT_READY asset=%s", asset)
            return None

        result = _ORIGINAL_ANALYZE(self, asset, normalized, price)
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
    log.info("BRAIN_FIX_APPLIED safe_demogate=true indicator_gate=true candle_normalization=true")

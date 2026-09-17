from __future__ import annotations

import logging
import math
from brain import CandiceBrain, STRATEGIES, EXPIRIES
from strategy_rotation import StrategyRotation

log = logging.getLogger("candice.brain_fix")
_ORIGINAL_ANALYZE = CandiceBrain.analyze


def _number(v):
    if isinstance(v, bool): return None
    if isinstance(v, (int, float)):
        try: return float(v) if math.isfinite(float(v)) else None
        except (TypeError, ValueError): return None
    if isinstance(v, str):
        try:
            x = float(v.strip()); return x if math.isfinite(x) else None
        except (TypeError, ValueError): return None
    if isinstance(v, dict):
        for key in ("value", "price", "rate", "quote", "close", "open", "high", "low", "v", "q", "p"):
            if key in v:
                x = _number(v[key])
                if x is not None: return x
    if isinstance(v, (list, tuple)):
        for item in reversed(v):
            x = _number(item)
            if x is not None: return x
    return None


def _normalize_candle(candle):
    if not isinstance(candle, dict): return None
    out = {}
    for key in ("open", "high", "low", "close"):
        value = _number(candle.get(key, candle.get(key[0])))
        if value is None: return None
        out[key] = value
    ts = _number(candle.get("timestamp", candle.get("t", candle.get("time"))))
    if ts is None: return None
    if ts > 1e10: ts /= 1000.0
    out["timestamp"] = float(ts)
    return out


def _finite(v): return _number(v) is not None


def analyze(self, asset, completed, live_price):
    try:
        client = getattr(self.feed, "client", None)
        if client is None or getattr(client, "account_mode", "UNKNOWN") != "DEMO": return None
        authoritative = set(getattr(self.feed, "_authoritative_flex_assets", set()))
        if asset not in authoritative: return None
        normalized = []
        for candle in completed:
            item = _normalize_candle(candle)
            if item is not None: normalized.append(item)
        normalized.sort(key=lambda x: x["timestamp"])
        price = _number(live_price)
        if len(normalized) < 60 or price is None: return None
        rsi = self._rsi([x["close"] for x in normalized]); adx = self._adx(normalized); atr = self._atr(normalized); macd = self._macd([x["close"] for x in normalized])
        if not (_finite(rsi) and _finite(adx) and _finite(atr)): return None
        if not isinstance(macd, (tuple, list)) or len(macd) < 2 or not (_finite(macd[0]) and _finite(macd[1])): return None
        result = _ORIGINAL_ANALYZE(self, asset, normalized, price)
        if not isinstance(result, dict): return None
        for key in ("asset", "direction", "expiry"):
            if result.get(key) is None: return None
        for key in ("confidence", "entry", "rsi", "adx"):
            if not _finite(result.get(key)): return None
        if result.get("direction") not in {"UP", "DOWN"}: return None
        return result
    except Exception:
        log.exception("BRAIN_ANALYZE_FAILED asset=%s", asset); return None


def _install_rotation():
    if getattr(CandiceBrain, "_strategy_rotation_installed", False): return
    original_init = CandiceBrain.__init__
    original_result = CandiceBrain._result

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.rotation = StrategyRotation(STRATEGIES)
        self.rotation_strategy_after_loss = None
        self._rotation_candidate = None
        base_send = self.send
        def send_with_rotation(message):
            result = base_send(message)
            candidate = getattr(self, "_rotation_candidate", None)
            if isinstance(result, dict) and result.get("message_id") and isinstance(candidate, dict):
                self.rotation.record_signal(candidate.get("asset"), candidate.get("strategy"))
                self._rotation_candidate = None
                log.info("ROTATION_SIGNAL_RECORDED asset=%s strategy=%s", candidate.get("asset"), candidate.get("strategy"))
            return result
        self.send = send_with_rotation
        log.info("STRATEGY_ROTATION_ENABLED pair_change=true loss_strategy_change=true")

    def rotate(scan, self):
        blocked = self.rotation_strategy_after_loss
        if not blocked or scan.get("strategy") != blocked: return scan
        direction = scan.get("direction"); votes = set(scan.get("votes", []))
        trend = 1 if scan.get("trend_15") == "UP" else -1 if scan.get("trend_15") == "DOWN" else 0
        alternatives = []
        for strategy in STRATEGIES:
            if strategy == blocked or f"{strategy} {direction}" not in votes: continue
            for expiry in EXPIRIES:
                key = f"{scan['asset']}|{direction}|{scan.get('pattern')}|{strategy}|{trend}|{expiry}"
                alternatives.append((self._history_rate(key), -abs(expiry - 5), strategy, expiry))
        if not alternatives: return None
        alternatives.sort(reverse=True)
        _, _, strategy, expiry = alternatives[0]
        out = dict(scan); out["strategy"] = strategy; out["expiry"] = expiry
        log.info("STRATEGY_ROTATED asset=%s previous=%s next=%s expiry=%s", scan.get("asset"), blocked, strategy, expiry)
        return out

    def best_scan(self):
        candidates = []
        last_pair = getattr(self.rotation, "last_pair", None)
        for asset in self.feed.status().get("assets", []):
            if last_pair and str(asset).upper() == last_pair: continue
            try:
                x = self.analyze(asset, self.feed.snapshot(asset), self.feed.live_price(asset))
                if x:
                    x = rotate(x, self)
                    if x: candidates.append(x)
            except Exception: log.exception("ASSET_ANALYSIS_FAILED asset=%s", asset)
        if not candidates:
            self._rotation_candidate = None; return None
        candidates.sort(key=lambda x: (x["confidence"], -x["expiry"]), reverse=True)
        self._rotation_candidate = candidates[0]
        return candidates[0]

    def result(self, s):
        ok = original_result(self, s)
        if ok and s.get("result"):
            self.rotation.record_result(s["result"])
            self.rotation_strategy_after_loss = s.get("strategy") if s["result"] == "LOSS" else None
            log.info("ROTATION_RESULT asset=%s result=%s next_strategy_excludes=%s", s.get("asset"), s.get("result"), self.rotation_strategy_after_loss)
        return ok

    CandiceBrain.__init__ = init
    CandiceBrain._best_scan = best_scan
    CandiceBrain._result = result
    CandiceBrain._strategy_rotation_installed = True
    log.info("BRAIN_ROTATION_PATCHED pair_no_repeat=true loss_strategy_change=true")


def apply():
    CandiceBrain.analyze = analyze
    _install_rotation()
    log.info("BRAIN_FIX_APPLIED safe_demogate=true indicator_gate=true candle_normalization=true strategy_rotation=true")

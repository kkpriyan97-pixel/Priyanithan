from __future__ import annotations
import json, os, threading, time
from pathlib import Path

from candice_memory import learning_context, record

STATE_PATH = Path(os.getenv("CANDICE_OWN_BRAIN_FILE", "data/candice_own_brain.json"))
LOCK = threading.Lock()
MIN_SAMPLES = max(8, int(os.getenv("CANDICE_OWN_MIN_SAMPLES", "12")))
MIN_EDGE = float(os.getenv("CANDICE_OWN_MIN_EDGE", "0.08"))


def _load():
    try:
        x = json.loads(STATE_PATH.read_text())
        return x if isinstance(x, dict) else {}
    except Exception:
        return {}


def _save(x):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(x, ensure_ascii=False, separators=(",", ":")))


def _rate(bucket):
    if not isinstance(bucket, dict):
        return None
    n = int(bucket.get("samples", 0) or 0)
    if n < MIN_SAMPLES:
        return None
    return float(bucket.get("win_rate", 0) or 0) / 100.0


def _pattern_rate(ctx, patterns):
    rates = []
    by = ctx.get("by_pattern", {}) or {}
    for p in patterns or ():
        r = _rate(by.get(str(p)))
        if r is not None:
            rates.append(r)
    return sum(rates) / len(rates) if rates else None


def _regime(ctx, regime):
    return _rate((ctx.get("by_regime", {}) or {}).get(str(regime).upper()))


def _direction(ctx, direction):
    return _rate((ctx.get("by_direction", {}) or {}).get(str(direction).upper()))


def _expiry(ctx, expiry):
    return _rate((ctx.get("by_expiry", {}) or {}).get(str(expiry)))


def _session_key():
    return time.strftime("%Y-%m-%d-%H")


def refine(asset: str, df, base):
    """Conservative adaptive brain.

    It never creates a trade by itself. It only vetoes or calibrates an already
    approved Candice analysis using outcome memory and the current candle context.
    Learning is descriptive until enough samples exist; this prevents early
    overfitting and keeps the DEMO/manual safety gate intact.
    """
    if not base or str(getattr(base, "decision", "")).upper() != "APPROVE":
        return base, {"decision": "PASS", "reason": "Base AI did not approve", "sample_count": 0}

    try:
        from candice_engine import closed_1m, technical_snapshot, resample_ohlc
        d = closed_1m(df)
        frames = {}
        if len(d) >= 60:
            frames["1m"] = technical_snapshot(d)
            for n in (3, 5, 10, 15):
                r = resample_ohlc(d, n)
                if len(r) >= 60:
                    frames[f"{n}m"] = technical_snapshot(r)
        primary = frames.get("5m") or frames.get("1m") or {}
    except Exception as exc:
        return base, {"decision": "PASS", "reason": f"Own brain data unavailable: {exc}", "sample_count": 0}

    direction = str(getattr(base, "direction", "")).upper()
    regime = "TREND" if float(primary.get("adx14", 0) or 0) >= 25 and float(primary.get("strength", 0) or 0) >= .8 else "RANGE" if float(primary.get("adx14", 0) or 0) < 18 else "TRANSITION"
    patterns = tuple(primary.get("candle_patterns", ()) or ())
    ctx = learning_context(asset)
    overall = ctx.get("overall", {}) or {}
    samples = int(overall.get("samples", 0) or 0)

    rates = [r for r in (_direction(ctx, direction), _regime(ctx, regime), _expiry(ctx, getattr(base, "expiry", 0)), _pattern_rate(ctx, patterns)) if r is not None]
    prior = sum(rates) / len(rates) if rates else None

    # Bayesian-style shrinkage toward 50% prevents tiny samples from dominating.
    if prior is not None:
        weight = min(.65, samples / 200.0)
        adaptive = .50 * (1.0 - weight) + prior * weight
    else:
        adaptive = .50

    conflict = int(primary.get("indicator_conflicts", 0) or 0)
    body = float(primary.get("body_ratio", 0) or 0)
    pattern_dir = str(primary.get("pattern_direction", "")).upper()

    penalty = 0.0
    reasons = []
    if conflict >= 3:
        penalty += .10
        reasons.append("indicator conflict")
    if body < .25:
        penalty += .05
        reasons.append("weak candle body")
    if pattern_dir and pattern_dir != direction:
        penalty += .10
        reasons.append("opposite candle structure")

    effective = adaptive - penalty
    # Only historical evidence with enough samples can veto. This avoids cold-start overfitting.
    veto = samples >= MIN_SAMPLES and rates and effective < (0.50 + MIN_EDGE)

    state = _load()
    session = state.setdefault("sessions", {})
    sk = _session_key()
    bucket = session.setdefault(sk, {"analyses": 0, "approvals": 0, "vetoes": 0})
    bucket["analyses"] = int(bucket.get("analyses", 0)) + 1
    if veto:
        bucket["vetoes"] = int(bucket.get("vetoes", 0)) + 1
    else:
        bucket["approvals"] = int(bucket.get("approvals", 0)) + 1
    with LOCK:
        _save(state)

    decision = "VETO" if veto else "PASS"
    reason = "; ".join(reasons) if reasons else "own historical session brain agrees"
    record({
        "ts": time.time(), "type": "own_brain_review", "asset": asset,
        "direction": direction, "expiry": getattr(base, "expiry", 0),
        "regime": regime, "patterns": patterns, "samples": samples,
        "adaptive_probability": round(adaptive, 4), "effective_probability": round(effective, 4),
        "decision": decision, "reason": reason,
    })

    if veto:
        from dataclasses import replace
        return replace(base, decision="REJECT", reason=f"Own Strategy Brain veto: {reason}; historical probability={effective:.0%}"), {
            "decision": "VETO", "probability": effective, "sample_count": samples, "reason": reason,
        }

    return base, {"decision": "PASS", "probability": effective, "sample_count": samples, "reason": reason}

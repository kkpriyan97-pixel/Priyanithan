"""Candice AI Strategy Evolution Engine V1.

DEMO/SHADOW ONLY: this module never places trades.
It continuously evaluates strategy candidates from observed outcomes,
keeps proven candidates by regime, and creates new candidates from a
small, deterministic mutation pool. It is intentionally conservative:
minimum samples, minimum confidence, and drawdown safeguards are required
before a candidate can influence signal qualification.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


STATE_PATH = Path(os.getenv("CANDICE_EVOLUTION_STATE", "data/candice_strategy_evolution.json"))
MIN_SAMPLES = max(5, int(os.getenv("CANDICE_EVOLUTION_MIN_SAMPLES", "8")))
PROMOTION_MIN_SAMPLES = max(MIN_SAMPLES, int(os.getenv("CANDICE_EVOLUTION_PROMOTION_SAMPLES", "20")))
MIN_WIN_RATE = float(os.getenv("CANDICE_EVOLUTION_MIN_WIN_RATE", "0.55"))
MAX_LOSS_STREAK = max(2, int(os.getenv("CANDICE_EVOLUTION_MAX_LOSS_STREAK", "3")))


@dataclass(frozen=True)
class Candidate:
    strategy_id: str
    family: str
    pattern_weight: float
    trend_weight: float
    momentum_weight: float
    mtf_weight: float
    reversal_penalty: float
    expiry_bias: int
    parent_id: str = ""


FAMILIES = ("trend", "breakout", "momentum", "reversal", "pattern", "hybrid")


def _candidate_id(c: Candidate) -> str:
    raw = json.dumps({k: v for k, v in asdict(c).items() if k != "strategy_id"}, sort_keys=True)
    return "EV1-" + hashlib.sha1(raw.encode()).hexdigest()[:12]


def _base_candidates() -> List[Candidate]:
    out = []
    for family, vals in {
        "trend": (1.0, 1.2, 0.8, 1.0, 1.0, 5),
        "breakout": (0.8, 1.0, 1.2, 1.0, 1.1, 3),
        "momentum": (0.8, 0.9, 1.3, 0.9, 1.2, 2),
        "reversal": (1.2, 0.7, 0.8, 1.0, 1.3, 2),
        "pattern": (1.0, 0.8, 0.9, 1.1, 1.1, 3),
        "hybrid": (1.0, 1.0, 1.0, 1.2, 1.0, 5),
    }.items():
        c = Candidate("", family, *vals)
        out.append(Candidate(_candidate_id(c), c.family, c.pattern_weight, c.trend_weight,
                             c.momentum_weight, c.mtf_weight, c.reversal_penalty, c.expiry_bias))
    return out


def _load() -> Dict[str, Any]:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"version": 1, "candidates": {}, "active_by_regime": {}, "history": []}


def _save(state: Dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(STATE_PATH)
    except Exception:
        pass


def _ensure_candidates(state: Dict[str, Any]) -> None:
    for c in _base_candidates():
        state["candidates"].setdefault(c.strategy_id, {**asdict(c), "wins": 0, "losses": 0, "samples": 0, "loss_streak": 0})


def _score(item: Dict[str, Any]) -> float:
    n = int(item.get("samples", 0))
    if n <= 0:
        return 0.0
    wr = int(item.get("wins", 0)) / n
    # Confidence grows with sample size but never becomes a guarantee.
    sample_factor = min(1.0, n / float(PROMOTION_MIN_SAMPLES))
    streak_penalty = max(0.0, 1.0 - 0.08 * int(item.get("loss_streak", 0)))
    return wr * sample_factor * streak_penalty


def _mutate(parent: Dict[str, Any]) -> Candidate:
    # Deterministic bounded mutation; no random overfitting loop.
    knobs = ("pattern_weight", "trend_weight", "momentum_weight", "mtf_weight", "reversal_penalty")
    idx = int(time.time() // 300) % len(knobs)
    data = dict(parent)
    key = knobs[idx]
    delta = 0.15 if int(time.time() // 300) % 2 else -0.15
    data[key] = max(0.4, min(1.6, float(data.get(key, 1.0)) + delta))
    data["parent_id"] = parent.get("strategy_id", "")
    data.pop("wins", None); data.pop("losses", None); data.pop("samples", None); data.pop("loss_streak", None)
    data["strategy_id"] = ""
    c = Candidate(**data)
    return Candidate(_candidate_id(c), c.family, c.pattern_weight, c.trend_weight,
                     c.momentum_weight, c.mtf_weight, c.reversal_penalty, c.expiry_bias, c.parent_id)


def record_outcome(strategy_id: str, outcome: str, regime: str = "unknown", asset: str = "") -> None:
    """Record a DEMO/shadow outcome. outcome must be WIN or LOSS."""
    outcome = outcome.upper().strip()
    if outcome not in {"WIN", "LOSS"}:
        return
    state = _load(); _ensure_candidates(state)
    item = state["candidates"].get(strategy_id)
    if not item:
        return
    item["samples"] = int(item.get("samples", 0)) + 1
    if outcome == "WIN":
        item["wins"] = int(item.get("wins", 0)) + 1
        item["loss_streak"] = 0
    else:
        item["losses"] = int(item.get("losses", 0)) + 1
        item["loss_streak"] = int(item.get("loss_streak", 0)) + 1
    state["history"].append({"ts": time.time(), "strategy_id": strategy_id, "outcome": outcome, "regime": regime, "asset": asset})
    state["history"] = state["history"][-1000:]
    _save(state)


def evolve(regime: str = "unknown") -> Dict[str, Any]:
    """Score, promote, and mutate strategy candidates for the current regime."""
    state = _load(); _ensure_candidates(state)
    ranked = sorted(state["candidates"].values(), key=_score, reverse=True)
    eligible = [x for x in ranked if int(x.get("samples", 0)) >= MIN_SAMPLES and
                int(x.get("loss_streak", 0)) < MAX_LOSS_STREAK and
                int(x.get("wins", 0)) / max(1, int(x.get("samples", 0))) >= MIN_WIN_RATE]
    pool = eligible or ranked[:2]
    if pool:
        best = pool[0]
        state["active_by_regime"][regime] = best["strategy_id"]
        # Periodically create a bounded child candidate so evolution continues.
        child = _mutate(best)
        state["candidates"].setdefault(child.strategy_id, {**asdict(child), "wins": 0, "losses": 0, "samples": 0, "loss_streak": 0})
        _save(state)
        return {"strategy_id": best["strategy_id"], "parent_id": best.get("parent_id", ""),
                "score": _score(best), "sample_count": best.get("samples", 0),
                "win_rate": int(best.get("wins", 0)) / max(1, int(best.get("samples", 0))),
                "candidate_created": child.strategy_id, "regime": regime}
    return {"strategy_id": "", "score": 0.0, "sample_count": 0, "win_rate": 0.0, "regime": regime}


def apply_context(base_plan: Dict[str, Any], regime: str = "unknown") -> Dict[str, Any]:
    """Return transparent strategy context without overriding hard safety gates."""
    state = _load(); _ensure_candidates(state)
    active_id = state.get("active_by_regime", {}).get(regime, "")
    item = state.get("candidates", {}).get(active_id, {})
    out = dict(base_plan or {})
    out["evolution_version"] = "V1"
    out["evolution_strategy_id"] = active_id
    out["evolution_score"] = round(_score(item), 4) if item else 0.0
    out["evolution_samples"] = int(item.get("samples", 0)) if item else 0
    out["evolution_win_rate"] = round(int(item.get("wins", 0)) / max(1, int(item.get("samples", 0))), 4) if item else 0.0
    # Never turn WAIT/REJECT into an approval.
    return out


__all__ = ["record_outcome", "evolve", "apply_context"]

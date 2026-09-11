"""Candice AI Experience Memory Brain.

Persistent, evidence-based learning only. This module never changes code,
AI prompts, risk gates, broker credentials, or trading execution behavior.
It records observations/signals/results and derives bounded strategy scores
for future analysis.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from pathlib import Path

LOCK = threading.RLock()
MAX_EVENTS = 50000
MAX_LESSONS = 5000
DEFAULT_PATH = Path(os.getenv("CANDICE_MEMORY_PATH", "data/candice_memory.json"))

class CandiceMemoryBrain:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self.events = []
        self.lessons = []
        self.strategy_stats = defaultdict(lambda: {"samples": 0, "wins": 0, "losses": 0})
        self._load()

    def _load(self):
        with LOCK:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.events = list(data.get("events", []))[-MAX_EVENTS:]
                self.lessons = list(data.get("lessons", []))[-MAX_LESSONS:]
                for key, value in (data.get("strategy_stats", {}) or {}).items():
                    self.strategy_stats[key] = value
            except (FileNotFoundError, ValueError, OSError):
                self.events, self.lessons = [], []

    def _save(self):
        with LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            payload = {
                "schema_version": 1,
                "updated_at": time.time(),
                "events": self.events[-MAX_EVENTS:],
                "lessons": self.lessons[-MAX_LESSONS:],
                "strategy_stats": dict(self.strategy_stats),
            }
            tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)

    def observe(self, snapshot: dict):
        event = {"type": "observation", "ts": time.time(), "data": dict(snapshot)}
        with LOCK:
            self.events.append(event)
            self._save()

    def record_signal(self, signal: dict):
        event = {"type": "signal", "ts": time.time(), "data": dict(signal)}
        with LOCK:
            self.events.append(event)
            self._save()

    def record_result(self, result: dict):
        data = dict(result)
        outcome = str(data.get("outcome", "")).upper()
        event = {"type": "result", "ts": time.time(), "data": data}
        key = self.strategy_key(data)
        with LOCK:
            self.events.append(event)
            stats = self.strategy_stats[key]
            stats["samples"] += 1
            if outcome == "WIN": stats["wins"] += 1
            elif outcome == "LOSS": stats["losses"] += 1
            self._make_lesson(data, key)
            self._save()

    def strategy_key(self, data: dict) -> str:
        return "|".join(str(data.get(k, "UNKNOWN")).upper() for k in ("asset", "timeframe", "strategy", "regime"))

    def _make_lesson(self, data: dict, key: str):
        outcome = str(data.get("outcome", "")).upper()
        if outcome not in {"WIN", "LOSS"}:
            return
        stats = self.strategy_stats[key]
        rate = round(100.0 * stats["wins"] / stats["samples"], 2) if stats["samples"] else 0.0
        lesson = {
            "ts": time.time(),
            "key": key,
            "outcome": outcome,
            "lesson": str(data.get("lesson") or ("setup confirmed by outcome" if outcome == "WIN" else "review entry, regime, timing, and expiry")),
            "win_rate_after_event": rate,
            "evidence_samples": stats["samples"],
        }
        self.lessons.append(lesson)

    def similar_experience(self, current: dict, limit: int = 20):
        wanted = {k: str(current.get(k, "")).upper() for k in ("asset", "timeframe", "strategy", "regime", "direction")}
        scored = []
        with LOCK:
            for event in self.events:
                if event.get("type") != "result":
                    continue
                data = event.get("data", {})
                score = sum(1 for k, value in wanted.items() if value and str(data.get(k, "")).upper() == value)
                if score:
                    scored.append((score, event))
        scored.sort(key=lambda x: (x[0], x[1].get("ts", 0)), reverse=True)
        return [e for _, e in scored[:max(1, int(limit))]]

    def strategy_score(self, current: dict) -> dict:
        key = self.strategy_key(current)
        with LOCK:
            stats = dict(self.strategy_stats.get(key, {}))
        samples = int(stats.get("samples", 0))
        wins = int(stats.get("wins", 0))
        rate = round(100.0 * wins / samples, 2) if samples else None
        return {"key": key, "samples": samples, "wins": wins, "losses": int(stats.get("losses", 0)), "historical_win_rate": rate}

    def context_for_ai(self, current: dict) -> dict:
        return {"strategy": self.strategy_score(current), "similar_results": self.similar_experience(current, 12)}

brain = CandiceMemoryBrain()

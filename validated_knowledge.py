"""Read-only bridge from validated self-learning knowledge into the live Candice brain.

Only rows explicitly stored as VALIDATED are visible here. Research candidates,
practice data, raw demo results, and unverified claims are never exposed to the
signal brain through this module.
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Any

_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
CACHE_TTL = max(15.0, float(os.getenv("VALIDATED_KNOWLEDGE_CACHE_TTL", "60")))
DB_PATH = os.getenv("LEARNING_DB_PATH", "candice_learning.sqlite3")


def _load() -> list[dict[str, Any]]:
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT knowledge_id, method_id, version, weight,
                   entry_conditions_json, no_trade_conditions_json,
                   error_filters_json, created_at
            FROM validated_knowledge
            WHERE status='VALIDATED'
            ORDER BY method_id, version DESC
            """
        ).fetchall()
        conn.close()
    except Exception:
        return []

    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        method_id = str(row["method_id"])
        if method_id in latest:
            continue
        latest[method_id] = {
            "knowledge_id": str(row["knowledge_id"]),
            "method_id": method_id,
            "version": int(row["version"]),
            "weight": max(0.0, min(1.0, float(row["weight"] or 0.0))),
            "entry_conditions": str(row["entry_conditions_json"] or ""),
            "no_trade_conditions": str(row["no_trade_conditions_json"] or ""),
            "error_filters": str(row["error_filters_json"] or ""),
            "created_at": float(row["created_at"] or 0.0),
        }
    return list(latest.values())


def load_validated_knowledge(now: float | None = None) -> list[dict[str, Any]]:
    now = time.monotonic() if now is None else float(now)
    item = _CACHE.get("all")
    if item and now - item[0] < CACHE_TTL:
        return [dict(x) for x in item[1]]
    records = _load()
    _CACHE["all"] = (now, records)
    return [dict(x) for x in records]


def knowledge_for_method(method_id: str) -> dict[str, Any] | None:
    wanted = str(method_id or "")
    for item in load_validated_knowledge():
        if item["method_id"] == wanted:
            return dict(item)
    return None

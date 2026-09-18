"""Candice Brain decision/state rules.

Read-only DEMO signal orchestration helpers. This module never places trades.
State is deliberately separated from market transport so it can later be backed
by PostgreSQL/another persistent store without changing the decision rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


COOLDOWN_SECONDS = 15 * 60
MIN_CONFIDENCE = 90
CYCLE_SECONDS = 5 * 60


def utc_now() -> float:
    return datetime.now(timezone.utc).timestamp()


@dataclass
class SignalRecord:
    cycle_id: int
    pair: str
    entry_candle_ts: int | float | str
    direction: str
    expiry_minutes: int
    entry_price: float
    signal_ts: float


@dataclass
class ActiveSignal:
    cycle_id: int
    pair: str
    display_name: str
    direction: str
    expiry_minutes: int
    entry_price: float
    entry_ts: float
    entry_candle_ts: int | float | str
    strategy: str = ""
    reason: str = ""
    confidence: int = 0


@dataclass
class BrainState:
    cycle_id: int = 0
    cycle_signal_sent: bool = False
    sent_keys: set[tuple[str, str]] = field(default_factory=set)
    cooldown_until: dict[str, float] = field(default_factory=dict)
    active_signal: ActiveSignal | None = None
    last_result: dict[str, Any] | None = None

    def start_cycle(self, cycle_id: int) -> None:
        self.cycle_id = cycle_id
        self.cycle_signal_sent = False
        self.sent_keys.clear()

    def is_in_cooldown(self, pair: str, now: float | None = None) -> bool:
        now = utc_now() if now is None else now
        until = float(self.cooldown_until.get(pair, 0) or 0)
        if until <= now:
            self.cooldown_until.pop(pair, None)
            return False
        return True

    def filter_candidates(self, assets: list[dict[str, Any]], now: float | None = None) -> list[dict[str, Any]]:
        now = utc_now() if now is None else now
        return [
            asset for asset in assets
            if asset.get("pair")
            and not asset.get("locked")
            and not asset.get("locked_trading")
            and not self.is_in_cooldown(str(asset["pair"]), now)
        ]

    def can_send_cycle_signal(self) -> bool:
        # Exactly one final signal maximum per 5-minute cycle.
        return not self.cycle_signal_sent and self.active_signal is None

    def duplicate_key(self, pair: str, entry_candle_ts: Any, direction: str = "") -> tuple[str, str]:
        return (str(pair), str(entry_candle_ts))

    def is_duplicate(self, pair: str, entry_candle_ts: Any, direction: str) -> bool:
        # Expiry is intentionally NOT part of this key: the same asset and
        # entry candle cannot produce a second signal with another expiry.
        return self.duplicate_key(pair, entry_candle_ts) in self.sent_keys

    def mark_signal_sent(
        self,
        *,
        pair: str,
        display_name: str,
        direction: str,
        expiry_minutes: int,
        entry_price: float,
        entry_ts: float,
        entry_candle_ts: Any,
        strategy: str = "",
        reason: str = "",
        confidence: int = 0,
    ) -> ActiveSignal:
        if not self.can_send_cycle_signal():
            raise RuntimeError("A final signal has already been sent for this cycle.")
        if int(confidence) < MIN_CONFIDENCE:
            raise ValueError(f"Signal confidence {confidence} is below {MIN_CONFIDENCE}.")

        if self.is_duplicate(pair, entry_candle_ts, direction):
            raise RuntimeError("Duplicate signal for the same asset/entry candle.")

        self.sent_keys.add(self.duplicate_key(pair, entry_candle_ts, direction))
        self.cycle_signal_sent = True
        self.active_signal = ActiveSignal(
            cycle_id=self.cycle_id,
            pair=str(pair),
            display_name=str(display_name),
            direction=str(direction).upper(),
            expiry_minutes=int(expiry_minutes),
            entry_price=float(entry_price),
            entry_ts=float(entry_ts),
            entry_candle_ts=entry_candle_ts,
            strategy=str(strategy),
            reason=str(reason),
            confidence=int(confidence),
        )
        return self.active_signal

    @staticmethod
    def classify_result(direction: str, entry: float, exit_price: float) -> str:
        if float(exit_price) == float(entry):
            return "TIE"
        direction = str(direction).upper()
        if direction == "UP":
            return "WIN" if float(exit_price) > float(entry) else "LOSS"
        if direction == "DOWN":
            return "WIN" if float(exit_price) < float(entry) else "LOSS"
        raise ValueError("direction must be UP or DOWN")

    def finish_signal(self, exit_price: float, result_ts: float | None = None) -> dict[str, Any]:
        if self.active_signal is None:
            raise RuntimeError("No active signal to finish.")

        signal = self.active_signal
        result = self.classify_result(signal.direction, signal.entry_price, float(exit_price))
        now = utc_now() if result_ts is None else float(result_ts)

        record = {
            "cycle_id": signal.cycle_id,
            "pair": signal.pair,
            "display_name": signal.display_name,
            "direction": signal.direction,
            "expiry_minutes": signal.expiry_minutes,
            "entry_price": signal.entry_price,
            "exit_price": float(exit_price),
            "entry_ts": signal.entry_ts,
            "result_ts": now,
            "strategy": signal.strategy,
            "reason": signal.reason,
            "confidence": signal.confidence,
            "result": result,
        }

        # LOSS -> 15-minute candidate exclusion.
        # WIN/TIE -> no cooldown; the asset may qualify again if its new live
        # market structure passes the full evaluation.
        if result == "LOSS":
            self.cooldown_until[signal.pair] = now + COOLDOWN_SECONDS

        self.last_result = record
        self.active_signal = None
        return record

    def prune_expired_cooldowns(self, now: float | None = None) -> None:
        now = utc_now() if now is None else now
        for pair, until in list(self.cooldown_until.items()):
            if float(until) <= now:
                self.cooldown_until.pop(pair, None)


def rank_signal_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank already-qualified candidates without forcing a signal.

    Confidence is the primary gate; supporting market-quality fields are only
    tie-breakers. No artificial asset rotation is applied.
    """
    qualified = [
        x for x in candidates
        if int(x.get("confidence") or 0) >= MIN_CONFIDENCE
        and str(x.get("direction") or "").upper() in {"UP", "DOWN"}
    ]
    return sorted(
        qualified,
        key=lambda x: (
            int(x.get("confidence") or 0),
            float(x.get("market_quality") or 0),
            int(x.get("profitability") or 0),
        ),
        reverse=True,
    )

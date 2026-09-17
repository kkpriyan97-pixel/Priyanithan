from __future__ import annotations

import logging
from collections import deque

log = logging.getLogger("candice.strategy_rotation")


class StrategyRotation:
    """Deterministic strategy/pair rotation state for signal selection.

    A pair cannot be emitted twice in a row. After a LOSS, the next signal
    must use a different strategy from the losing setup; the new strategy is
    selected automatically from the available strategy names.
    """

    def __init__(self, strategies):
        self.strategies = tuple(strategies)
        self.last_pair = None
        self.last_strategy = None
        self._after_loss = False
        self._recent_pairs = deque(maxlen=8)

    def choose_pair(self, pairs):
        candidates = sorted({str(p).upper() for p in pairs if p})
        if not candidates:
            return None
        fresh = [p for p in candidates if p != self.last_pair]
        pool = fresh or candidates
        # Prefer a pair not used in the recent rotation when possible.
        unused = [p for p in pool if p not in self._recent_pairs]
        return (unused or pool)[0]

    def choose_strategy(self, available, losing_strategy=None):
        names = [str(s) for s in available if s]
        if not names:
            return None
        blocked = losing_strategy if self._after_loss and losing_strategy else None
        choices = [s for s in names if s != blocked]
        if not choices:
            return None
        if self.last_strategy in choices:
            idx = choices.index(self.last_strategy)
            return choices[(idx + 1) % len(choices)]
        return choices[0]

    def record_signal(self, pair, strategy):
        self.last_pair = str(pair).upper()
        self.last_strategy = str(strategy)
        self._recent_pairs.append(self.last_pair)
        self._after_loss = False

    def record_result(self, result):
        if str(result).upper() == "LOSS":
            self._after_loss = True
        elif str(result).upper() in {"WIN", "TIE"}:
            self._after_loss = False

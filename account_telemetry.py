"""Candice account telemetry bridge.

Never guesses DEMO/REAL from configuration. It mirrors the account snapshot
reported by the live Olymptrade feed (event 55). Until that snapshot is
received, Telegram shows the account as unverified instead of displaying a
hard-coded demo mode or fake balance.
"""
from __future__ import annotations

import logging
import sys
import threading
import time

LOG = logging.getLogger("candice.account")


def _sync_once() -> bool:
    sc = sys.modules.get("sitecustomize")
    main = sys.modules.get("__main__")
    if sc is None or main is None:
        return False

    feed = getattr(main, "live_feed", None)
    if feed is None:
        sc.MODE = "READ_ONLY_UNVERIFIED"
        sc.BALANCE_TEXT = "NOT_VERIFIED"
        return False

    mode = str(getattr(feed, "account_mode", "UNKNOWN") or "UNKNOWN").upper()
    balance = getattr(feed, "account_balance", None)
    currency = str(getattr(feed, "account_currency", "") or "").upper()
    updated = float(getattr(feed, "account_updated", 0.0) or 0.0)

    if mode in {"DEMO", "REAL"} and balance is not None and updated > 0:
        age = max(0.0, time.time() - updated)
        sc.MODE = f"READ_ONLY_{mode}"
        sc.BALANCE_TEXT = (
            f"{float(balance):.2f}{(' ' + currency) if currency else ''}"
            f" | VERIFIED {age:.0f}s ago"
        )
        LOG.info(
            "ACCOUNT_TELEMETRY_SYNC mode=%s balance=%s currency=%s age=%.1fs source=OLYMPTRADE_EVENT_55",
            mode,
            balance,
            currency or "-",
            age,
        )
        return True

    sc.MODE = "READ_ONLY_UNVERIFIED"
    sc.BALANCE_TEXT = "NOT_VERIFIED (waiting for Olymptrade account snapshot)"
    return False


def _watch() -> None:
    last = None
    while True:
        try:
            ok = _sync_once()
            sc = sys.modules.get("sitecustomize")
            if sc is not None:
                feed = getattr(sys.modules.get("__main__"), "live_feed", None)
                state = (
                    str(getattr(feed, "account_mode", "UNKNOWN")),
                    getattr(feed, "account_balance", None),
                    getattr(feed, "account_currency", ""),
                    getattr(feed, "account_updated", 0.0),
                ) if feed is not None else ("UNKNOWN", None, "", 0.0)
                if state != last:
                    last = state
                    LOG.info(
                        "ACCOUNT_TELEMETRY_STATE mode=%s balance=%s currency=%s updated=%s verified=%s",
                        state[0], state[1], state[2] or "-", state[3], ok,
                    )
        except Exception:
            LOG.exception("ACCOUNT_TELEMETRY_SYNC failed")
        time.sleep(1)


threading.Thread(target=_watch, name="candice-account-telemetry", daemon=True).start()

"""Candice read-only account telemetry bridge.

Prefer the REAL account from the broker's raw balance snapshot (event 55) when
it is present. Never infer REAL from a stale demo value and never place orders.
If no real account is exposed by the authenticated session, report that fact
instead of relabelling the demo balance as real.
"""
from __future__ import annotations

import logging
import sys
import threading
import time

LOG = logging.getLogger("candice.account")


def _number(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _raw_accounts(feed):
    """Return the latest event-55 account rows when the feed client exposes them."""
    client = getattr(feed, "client", None)
    raw = getattr(client, "current_balance", None) if client is not None else None
    rows = raw.get("d") if isinstance(raw, dict) else None
    return rows if isinstance(rows, list) else []


def _pick_real(rows):
    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        group = str(row.get("group", "")).strip().lower()
        if group != "real":
            continue
        amount = _number(row.get("amount", row.get("amount_real", row.get("amount_free", row.get("balance")))))
        if amount is None:
            continue
        currency = str(row.get("currency", row.get("currency_code", "")) or "").upper()
        candidates.append((amount, currency, row.get("account_id")))
    return max(candidates, key=lambda x: x[0]) if candidates else None


def _pick_demo(rows):
    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("group", "")).strip().lower() != "demo":
            continue
        amount = _number(row.get("amount", row.get("amount_real", row.get("amount_free", row.get("balance")))))
        if amount is None:
            continue
        currency = str(row.get("currency", row.get("currency_code", "")) or "").upper()
        candidates.append((amount, currency, row.get("account_id")))
    return max(candidates, key=lambda x: x[0]) if candidates else None


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

    # Event 55 is the authoritative live balance snapshot. Prefer REAL if it
    # exists in that snapshot, even when the upstream client previously chose
    # the demo account context during startup.
    rows = _raw_accounts(feed)
    real = _pick_real(rows)
    if real is not None:
        balance, currency, account_id = real
        feed.account_mode = "REAL"
        feed.account_balance = balance
        feed.account_currency = currency
        # The client snapshot was just read; keep a local verification clock.
        if not getattr(feed, "account_updated", 0.0):
            feed.account_updated = time.time()
        age = max(0.0, time.time() - float(getattr(feed, "account_updated", 0.0) or 0.0))
        sc.MODE = "READ_ONLY_REAL"
        sc.BALANCE_TEXT = f"{balance:.2f}{(' ' + currency) if currency else ''} | VERIFIED {age:.0f}s ago"
        LOG.info(
            "ACCOUNT_REAL_SNAPSHOT mode=REAL balance=%s currency=%s account_id=%s age=%.1fs source=OLYMPTRADE_EVENT_55",
            balance, currency or "-", account_id or "-", age,
        )
        return age <= 90

    demo = _pick_demo(rows)
    if demo is not None:
        balance, currency, account_id = demo
        sc.MODE = "READ_ONLY_DEMO"
        sc.BALANCE_TEXT = f"{balance:.2f}{(' ' + currency) if currency else ''} | DEMO VERIFIED"
        LOG.info(
            "ACCOUNT_REAL_UNAVAILABLE mode=DEMO balance=%s currency=%s account_id=%s reason=no REAL account in latest event 55",
            balance, currency or "-", account_id or "-",
        )
        return False

    sc.MODE = "READ_ONLY_UNVERIFIED"
    sc.BALANCE_TEXT = "REAL_NOT_VERIFIED (no real account snapshot)"
    return False


def _watch() -> None:
    last = None
    while True:
        try:
            ok = _sync_once()
            main = sys.modules.get("__main__")
            feed = getattr(main, "live_feed", None) if main is not None else None
            state = (
                getattr(feed, "account_mode", "UNKNOWN"),
                getattr(feed, "account_balance", None),
                getattr(feed, "account_currency", ""),
                getattr(feed, "account_updated", 0.0),
                ok,
            ) if feed is not None else ("UNKNOWN", None, "", 0.0, False)
            if state != last:
                last = state
                LOG.info(
                    "ACCOUNT_TELEMETRY_STATE mode=%s balance=%s currency=%s updated=%s verified=%s",
                    state[0], state[1], state[2] or "-", state[3], state[4],
                )
        except Exception:
            LOG.exception("ACCOUNT_TELEMETRY_SYNC failed")
        time.sleep(1)


threading.Thread(target=_watch, name="candice-account-telemetry", daemon=True).start()

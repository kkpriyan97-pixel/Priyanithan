"""Candice read-only account telemetry bridge.

Mirror the account explicitly selected by the authenticated Olymptrade session.
Support both Demo and Real without guessing from balance size. Never place
orders and never relabel an unverified account.
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
    client = getattr(feed, "client", None)
    raw = getattr(client, "current_balance", None) if client is not None else None
    rows = raw.get("d") if isinstance(raw, dict) else None
    return rows if isinstance(rows, list) else []


def _norm_id(v):
    if v is None:
        return ""
    return str(v).strip()


def _row_account_id(row):
    return _norm_id(row.get("account_id", row.get("accountId", row.get("id"))))


def _row_mode(row):
    return str(row.get("group", row.get("mode", row.get("type", ""))) or "").strip().lower()


def _explicit_selected(row):
    """Return True only for an explicit active/selected marker."""
    for key in ("selected", "is_selected", "active", "is_active", "current", "is_current"):
        if key in row and isinstance(row.get(key), bool) and row.get(key):
            return True
        if key in row and str(row.get(key, "")).strip().lower() in {"1", "true", "yes", "selected", "active", "current"}:
            return True
    status = str(row.get("status", "") or "").strip().lower()
    return status in {"active", "selected", "current"}


def _client_selected_id(feed):
    """Best-effort lookup of an active account id exposed by the WS client."""
    client = getattr(feed, "client", None)
    if client is None:
        return ""
    for obj in (client, feed, getattr(client, "account", None), getattr(client, "accounts", None)):
        if obj is None:
            continue
        for key in ("selected_account_id", "active_account_id", "current_account_id", "account_id", "selectedAccountId", "activeAccountId", "currentAccountId"):
            try:
                value = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
            except Exception:
                value = None
            value = _norm_id(value)
            if value:
                return value
    return ""


def _balance(row):
    return _number(row.get("amount", row.get("amount_real", row.get("amount_free", row.get("balance")))))


def _currency(row):
    return str(row.get("currency", row.get("currency_code", "")) or "").upper()


def _find_selected(rows, feed):
    """Find the account explicitly selected by the platform/session."""
    selected_id = _client_selected_id(feed)
    if selected_id:
        for row in rows:
            if isinstance(row, dict) and _row_account_id(row) == selected_id and _balance(row) is not None:
                return row, "client_selected_id"

    marked = [row for row in rows if isinstance(row, dict) and _explicit_selected(row) and _balance(row) is not None]
    if len(marked) == 1:
        return marked[0], "event_selected_marker"
    if len(marked) > 1:
        # Multiple marked rows are ambiguous; do not guess.
        return None, "ambiguous_selected_marker"
    return None, "no_explicit_selection"


def _apply(row, source, feed, verified=True):
    sc = sys.modules.get("sitecustomize")
    if sc is None:
        return False
    mode = _row_mode(row)
    if mode not in {"real", "demo"}:
        sc.MODE = "READ_ONLY_UNVERIFIED"
        sc.BALANCE_TEXT = "ACCOUNT_SELECTION_UNVERIFIED"
        return False

    balance = _balance(row)
    currency = _currency(row)
    account_id = _row_account_id(row)
    if balance is None:
        sc.MODE = "READ_ONLY_UNVERIFIED"
        sc.BALANCE_TEXT = "ACCOUNT_SELECTION_UNVERIFIED"
        return False

    feed.account_mode = mode.upper()
    feed.account_balance = balance
    feed.account_currency = currency
    feed.account_id = account_id
    feed.account_selection_source = source
    feed.account_updated = time.time()
    sc.MODE = "READ_ONLY_REAL" if mode == "real" else "READ_ONLY_DEMO"
    label = "REAL" if mode == "real" else "DEMO"
    sc.BALANCE_TEXT = f"{balance:.2f}{(' ' + currency) if currency else ''} | {label} SELECTED"
    LOG.info(
        "ACCOUNT_SELECTED_SNAPSHOT mode=%s balance=%s currency=%s account_id=%s source=%s verified=%s",
        label, balance, currency or "-", account_id or "-", source, verified,
    )
    return bool(verified)


def _sync_once() -> bool:
    sc = sys.modules.get("sitecustomize")
    main = sys.modules.get("__main__")
    if sc is None or main is None:
        return False
    feed = getattr(main, "live_feed", None)
    if feed is None:
        sc.MODE = "READ_ONLY_UNVERIFIED"
        sc.BALANCE_TEXT = "ACCOUNT_SELECTION_UNVERIFIED"
        return False

    rows = _raw_accounts(feed)
    if not rows:
        sc.MODE = "READ_ONLY_UNVERIFIED"
        sc.BALANCE_TEXT = "ACCOUNT_SELECTION_UNVERIFIED (no account snapshot)"
        return False

    row, source = _find_selected(rows, feed)
    if row is not None:
        return _apply(row, source, feed, verified=True)

    # Do not silently choose the largest REAL/DEMO balance. That was the old
    # bug: it could show Real while the user had Demo selected, or vice versa.
    sc.MODE = "READ_ONLY_UNVERIFIED"
    sc.BALANCE_TEXT = "ACCOUNT_SELECTION_UNVERIFIED"
    LOG.warning(
        "ACCOUNT_SELECTION_UNVERIFIED rows=%s reason=%s | waiting for explicit selected-account metadata",
        len(rows), source,
    )
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
                getattr(feed, "account_id", ""),
                getattr(feed, "account_selection_source", ""),
                ok,
            ) if feed is not None else ("UNKNOWN", None, "", "", "", False)
            if state != last:
                last = state
                LOG.info(
                    "ACCOUNT_TELEMETRY_STATE mode=%s balance=%s currency=%s account_id=%s source=%s verified=%s",
                    state[0], state[1], state[2] or "-", state[3] or "-", state[4] or "-", state[5],
                )
        except Exception:
            LOG.exception("ACCOUNT_TELEMETRY_SYNC failed")
        time.sleep(1)


threading.Thread(target=_watch, name="candice-account-telemetry", daemon=True).start()

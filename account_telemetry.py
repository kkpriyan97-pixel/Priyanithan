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


def _snapshot(feed):
    client = getattr(feed, "client", None)
    raw = getattr(client, "current_balance", None) if client is not None else None
    return raw if isinstance(raw, dict) else {}


def _raw_accounts(feed):
    raw = _snapshot(feed)
    rows = raw.get("d")
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
    for key in ("selected", "is_selected", "active", "is_active", "current", "is_current"):
        if key in row:
            value = row.get(key)
            if isinstance(value, bool) and value:
                return True
            if str(value).strip().lower() in {"1", "true", "yes", "selected", "active", "current"}:
                return True
    return str(row.get("status", "") or "").strip().lower() in {"active", "selected", "current"}


def _find_id_by_key(obj, wanted_keys, depth=0):
    """Safely find an account id referenced by an explicitly named selector key."""
    if depth > 5:
        return ""
    if isinstance(obj, dict):
        for key, value in obj.items():
            k = str(key).strip().lower()
            if k in wanted_keys:
                if isinstance(value, (str, int, float)):
                    value = _norm_id(value)
                    if value:
                        return value
                if isinstance(value, dict):
                    for id_key in ("account_id", "accountId", "id"):
                        value_id = _norm_id(value.get(id_key))
                        if value_id:
                            return value_id
            found = _find_id_by_key(value, wanted_keys, depth + 1)
            if found:
                return found
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            found = _find_id_by_key(value, wanted_keys, depth + 1)
            if found:
                return found
    return ""


def _client_selected_id(feed, raw):
    client = getattr(feed, "client", None)
    objects = [raw, client, feed]
    for obj in objects:
        if obj is None:
            continue
        found = _find_id_by_key(
            obj,
            {
                "selected_account_id", "selectedaccountid", "active_account_id",
                "activeaccountid", "current_account_id", "currentaccountid",
                "selected_id", "active_id", "current_id", "selectedaccount",
                "activeaccount", "currentaccount",
            },
        )
        if found:
            return found
    return ""


def _balance(row):
    return _number(row.get("amount", row.get("amount_real", row.get("amount_free", row.get("balance")))))


def _currency(row):
    return str(row.get("currency", row.get("currency_code", "")) or "").upper()


def _find_selected(rows, feed, raw):
    selected_id = _client_selected_id(feed, raw)
    if selected_id:
        matches = [row for row in rows if isinstance(row, dict) and _row_account_id(row) == selected_id and _balance(row) is not None]
        if len(matches) == 1:
            return matches[0], "explicit_selected_account_id"
        if len(matches) > 1:
            return None, "ambiguous_selected_account_id"

    marked = [row for row in rows if isinstance(row, dict) and _explicit_selected(row) and _balance(row) is not None]
    if len(marked) == 1:
        return marked[0], "event_selected_marker"
    if len(marked) > 1:
        return None, "ambiguous_selected_marker"
    return None, "no_explicit_selection"


def _apply(row, source, feed):
    sc = sys.modules.get("sitecustomize")
    if sc is None:
        return False
    mode = _row_mode(row)
    balance = _balance(row)
    if mode not in {"real", "demo"} or balance is None:
        sc.MODE = "READ_ONLY_UNVERIFIED"
        sc.BALANCE_TEXT = "ACCOUNT_SELECTION_UNVERIFIED"
        return False

    currency = _currency(row)
    account_id = _row_account_id(row)
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
        "ACCOUNT_SELECTED_SNAPSHOT mode=%s balance=%s currency=%s account_id=%s source=%s",
        label, balance, currency or "-", account_id or "-", source,
    )
    return True


def _clear_unverified(feed, reason):
    sc = sys.modules.get("sitecustomize")
    if sc is not None:
        sc.MODE = "READ_ONLY_UNVERIFIED"
        sc.BALANCE_TEXT = "ACCOUNT_SELECTION_UNVERIFIED"
    feed.account_mode = "UNVERIFIED"
    feed.account_balance = None
    feed.account_currency = ""
    feed.account_id = ""
    feed.account_selection_source = reason
    feed.account_updated = 0.0


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

    raw = _snapshot(feed)
    rows = raw.get("d") if isinstance(raw.get("d"), list) else []
    if not rows:
        _clear_unverified(feed, "no_account_snapshot")
        return False

    row, source = _find_selected(rows, feed, raw)
    if row is not None:
        return _apply(row, source, feed)

    _clear_unverified(feed, source)
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

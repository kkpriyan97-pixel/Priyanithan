"""Candice read-only account telemetry bridge.

Mirror the account selected by the authenticated Olymptrade session when the
session exposes an account id/group. Never infer Demo/Real from balance size.
Never place orders.
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


def _norm_id(v):
    return "" if v is None else str(v).strip()


def _row_account_id(row):
    return _norm_id(row.get("account_id", row.get("accountId", row.get("id"))))


def _row_mode(row):
    return str(row.get("group", row.get("mode", row.get("type", ""))) or "").strip().lower()


def _balance(row):
    return _number(row.get("amount", row.get("amount_real", row.get("amount_free", row.get("balance")))))


def _currency(row):
    return str(row.get("currency", row.get("currency_code", "")) or "").upper()


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
    if depth > 6:
        return ""
    if isinstance(obj, dict):
        for key, value in obj.items():
            k = str(key).strip().lower()
            if k in wanted_keys:
                if isinstance(value, (str, int, float)):
                    found = _norm_id(value)
                    if found:
                        return found
                if isinstance(value, dict):
                    for id_key in ("account_id", "accountId", "id"):
                        found = _norm_id(value.get(id_key))
                        if found:
                            return found
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
    for obj in (raw, client, feed):
        if obj is None:
            continue
        found = _find_id_by_key(obj, {
            "selected_account_id", "selectedaccountid", "active_account_id",
            "activeaccountid", "current_account_id", "currentaccountid",
            "selected_id", "active_id", "current_id", "selectedaccount",
            "activeaccount", "currentaccount",
        })
        if found:
            return found
    return ""


def _session_selected(rows, feed):
    """Use the authenticated websocket client's explicit account selection.

    The current Olymptrade client exposes account_id/account_group after its
    account-info handshake. This is stronger evidence than choosing the
    largest balance and avoids relabelling an arbitrary account.
    """
    client = getattr(feed, "client", None)
    if client is None:
        return None, "no_client"
    session_id = _norm_id(getattr(client, "account_id", None))
    session_group = str(getattr(client, "account_group", "") or "").strip().lower()
    if session_id and session_group in {"demo", "real"}:
        matches = [
            r for r in rows
            if isinstance(r, dict)
            and _row_account_id(r) == session_id
            and _row_mode(r) == session_group
            and _balance(r) is not None
        ]
        if len(matches) == 1:
            return matches[0], "authenticated_session_account"
        if len(matches) > 1:
            return None, "ambiguous_authenticated_session_account"
    return None, "session_selection_unavailable"


def _find_selected(rows, feed, raw):
    # 1. Explicit selector metadata, if Olymptrade sends it.
    selected_id = _client_selected_id(feed, raw)
    if selected_id:
        matches = [r for r in rows if isinstance(r, dict) and _row_account_id(r) == selected_id and _balance(r) is not None]
        if len(matches) == 1:
            return matches[0], "explicit_selected_account_id"
        if len(matches) > 1:
            return None, "ambiguous_selected_account_id"

    # 2. Explicit selected/current/active marker on an account row.
    marked = [r for r in rows if isinstance(r, dict) and _explicit_selected(r) and _balance(r) is not None]
    if len(marked) == 1:
        return marked[0], "event_selected_marker"
    if len(marked) > 1:
        return None, "ambiguous_selected_marker"

    # 3. Authenticated client selection. This is the important fallback for
    # the current websocket implementation, which exposes account_id/group
    # but does not attach a selected flag to event-55 rows.
    row, source = _session_selected(rows, feed)
    if row is not None:
        return row, source
    return None, source


def _apply(row, source, feed):
    sc = sys.modules.get("sitecustomize")
    if sc is None:
        return False
    mode = _row_mode(row)
    balance = _balance(row)
    if mode not in {"real", "demo"} or balance is None:
        _clear_unverified(feed, "invalid_selected_account")
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
    LOG.info("ACCOUNT_SELECTED_SNAPSHOT mode=%s balance=%s currency=%s account_id=%s source=%s", label, balance, currency or "-", account_id or "-", source)
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
    LOG.warning("ACCOUNT_SELECTION_UNVERIFIED rows=%s reason=%s | waiting for explicit selected-account metadata or authenticated session selection", len(rows), source)
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
                getattr(feed, "account_selection_source", ""), ok,
            ) if feed is not None else ("UNKNOWN", None, "", "", "", False)
            if state != last:
                last = state
                LOG.info("ACCOUNT_TELEMETRY_STATE mode=%s balance=%s currency=%s account_id=%s source=%s verified=%s", state[0], state[1], state[2] or "-", state[3] or "-", state[4] or "-", state[5])
        except Exception:
            LOG.exception("ACCOUNT_TELEMETRY_SYNC failed")
        time.sleep(1)


threading.Thread(target=_watch, name="candice-account-telemetry", daemon=True).start()

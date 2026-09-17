from __future__ import annotations

import logging
import re
import time

log = logging.getLogger("candice.flex_probe")
_PAIR_RE = re.compile(r"^[A-Z0-9][A-Z0-9_./-]{2,39}$")
_ASSET_KEYS = {
    "pair", "symbol", "asset", "instrument", "ticker", "short_name",
    "display_name", "symbol_name", "pair_name", "asset_id", "asset_name",
    "asset_code", "symbol_code", "ticker_symbol", "instrument_name",
    "instrument_code", "shortName", "pair_name"
}
_BAD = {
    "FLEX", "FLEX TIME", "FLEX_TIME", "FIXED TIME", "FOREX", "STOCKS",
    "ASSET", "SYMBOL", "PAIR", "INSTRUMENT", "TICKER"
}


def _candidate(v):
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    if s in _BAD or " " in s or not _PAIR_RE.fullmatch(s):
        return None
    if not any(c.isalpha() for c in s):
        return None
    return s


def _extract(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            lk = str(key).strip().lower()
            if lk in {k.lower() for k in _ASSET_KEYS}:
                values = value if isinstance(value, list) else [value]
                for item in values:
                    if isinstance(item, dict):
                        for subkey in _ASSET_KEYS:
                            c = _candidate(item.get(subkey))
                            if c:
                                out.add(c)
                                break
                    else:
                        c = _candidate(item)
                        if c:
                            out.add(c)
            elif isinstance(key, str) and isinstance(value, dict):
                # Some authenticated catalogue payloads use pair/symbol as the
                # dictionary key and put metadata in the child object.
                c = _candidate(key)
                if c and any(str(k).strip().lower() in {x.lower() for x in _ASSET_KEYS} for k in value):
                    out.add(c)
                _extract(value, out)
            elif isinstance(value, (dict, list)):
                _extract(value, out)
    elif isinstance(node, list):
        for item in node:
            _extract(item, out)


def _publish(feed, found, source):
    found = {a for a in found if _candidate(a)}
    if not found:
        return
    feed._flex_seen_assets.update(found)
    feed._authoritative_flex_assets = set(feed._flex_seen_assets)
    feed.assets = set(feed._flex_seen_assets)
    log.info(
        "FLEX_ASSET_DISCOVERED source=%s new=%s total=%s assets=%s",
        source, len(found), len(feed.assets), sorted(found)
    )
    for asset in sorted(found):
        feed.schedule_asset(asset)


def _attach(feed):
    if getattr(feed, "_resilient_asset_discovery_attached", False):
        return

    feed._flex_seen_assets = set(getattr(feed, "_flex_seen_assets", set()))
    feed._authoritative_flex_assets = set(getattr(feed, "_authoritative_flex_assets", set()))
    feed._flex_last_event = float(getattr(feed, "_flex_last_event", 0.0))

    async def on_event_183(msg):
        if not isinstance(msg, dict):
            return
        found = set()
        _extract(msg.get("d"), found)
        log.info("FLEX_EVENT_183_SHAPE top=%s found=%s", type(msg.get("d")).__name__, sorted(found))
        if found:
            feed._flex_last_event = time.time()
            _publish(feed, found, "authenticated_event_183")

    async def on_authenticated_event(msg):
        if not isinstance(msg, dict):
            return
        event = msg.get("e")
        if event == 183:
            return
        # Discover only from authenticated websocket messages and only from
        # explicit pair/symbol/instrument fields. No OTC/REAL inference here.
        found = set()
        _extract(msg.get("d"), found)
        if found:
            _publish(feed, found, f"authenticated_event_{event}")

    feed.client.on(183, on_event_183)
    feed.client.on("*", on_authenticated_event)
    feed._resilient_asset_discovery_attached = True
    log.info("FLEX_RESILIENT_DISCOVERY_ATTACHED source=authenticated_websocket events=183+authenticated_candidates")


def _patch():
    try:
        from market_feed import LiveMarketFeed
    except Exception:
        return
    if getattr(LiveMarketFeed, "_resilient_asset_discovery_patched", False):
        return
    original_init = LiveMarketFeed.__init__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        _attach(self)

    LiveMarketFeed.__init__ = init
    LiveMarketFeed._resilient_asset_discovery_patched = True
    log.info("FLEX_RESILIENT_DISCOVERY_PATCHED authenticated_only=true market_type_inference=false")


_patch()

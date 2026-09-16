from __future__ import annotations

import asyncio
import logging
import re
import time

log = logging.getLogger("candice.flex_probe")
_PAIR_RE = re.compile(r"^[A-Z0-9][A-Z0-9_./-]{2,39}$")
_ASSET_KEYS = {
    "pair", "symbol", "asset", "instrument", "code", "ticker",
    "short_name", "display_name", "symbol_name", "pair_name",
    "asset_id", "asset_name", "asset_code", "symbol_code", "ticker_symbol",
    "instrument_name", "instrument_code", "shortName", "name", "title"
}
_BAD = {"FLEX", "FLEX TIME", "FLEX_TIME", "FIXED TIME", "FOREX", "STOCKS", "ASSET", "SYMBOL"}


def _candidate(v):
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    if s in _BAD or not _PAIR_RE.fullmatch(s) or not any(c.isalpha() for c in s):
        return None
    if " " in s or len(s) > 40:
        return None
    return s


def _extract(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() in {k.lower() for k in _ASSET_KEYS}:
                values = value if isinstance(value, list) else [value]
                for item in values:
                    if isinstance(item, dict):
                        for subkey in _ASSET_KEYS:
                            candidate = _candidate(item.get(subkey))
                            if candidate:
                                out.add(candidate)
                                break
                    else:
                        candidate = _candidate(item)
                        if candidate:
                            out.add(candidate)
            elif isinstance(value, (dict, list)):
                _extract(value, out)
    elif isinstance(node, list):
        for item in node:
            _extract(item, out)


def _attach(feed):
    if getattr(feed, "_account_flex_probe_attached", False):
        return

    feed._authoritative_flex_assets = set()
    feed._flex_event_batch = set()
    feed._flex_seen_assets = set()
    feed._flex_finalize_task = None
    feed._flex_last_event = 0.0

    async def _finalize():
        # Event 183 is observed as a stream/fragmented response, not a complete
        # snapshot. Give it a collection window so Brain can scan the full
        # authenticated Flex universe instead of replacing it every 2 seconds.
        await asyncio.sleep(30.0)
        found = set(feed._flex_event_batch)
        feed._flex_event_batch.clear()
        if not found:
            log.warning("FLEX_ACCOUNT_ASSETS_EMPTY event=183")
            return

        feed._flex_seen_assets.update(found)
        authoritative = set(feed._flex_seen_assets)
        feed._authoritative_flex_assets = authoritative
        old = set(feed.assets)
        feed.assets = set(authoritative)

        log.info(
            "FLEX_ACCOUNT_ASSETS_FULL_SCAN event=183 source=authenticated_demo_session batch=%s total=%s assets=%s",
            len(found), len(authoritative), sorted(authoritative),
        )

        for asset in sorted(authoritative - set(feed.subscribed)):
            feed.schedule_asset(asset)

    async def on_message(msg):
        # HARD RULE: only authenticated Flex event 183 defines the asset universe.
        if not isinstance(msg, dict) or msg.get("e") != 183:
            return
        found = set()
        _extract(msg.get("d"), found)
        log.info(
            "FLEX_EVENT_183_SHAPE top=%s found=%s",
            type(msg.get("d")).__name__, sorted(found),
        )
        if not found:
            return
        feed._flex_event_batch.update(found)
        feed._flex_last_event = time.time()
        task = feed._flex_finalize_task
        if task and not task.done():
            return
        feed._flex_finalize_task = asyncio.create_task(_finalize())

    feed.client.on(183, on_message)
    feed._account_flex_probe_attached = True


def _patch():
    try:
        from market_feed import LiveMarketFeed
    except Exception:
        return
    if getattr(LiveMarketFeed, "_account_flex_probe_patched", False):
        return
    original_init = LiveMarketFeed.__init__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        _attach(self)

    LiveMarketFeed.__init__ = init
    LiveMarketFeed._account_flex_probe_patched = True
    log.info("FLEX_ACCOUNT_PROBE_PATCHED source=authenticated_websocket event_183_only=true full_scan=true demo_real_supported=true")


_patch()

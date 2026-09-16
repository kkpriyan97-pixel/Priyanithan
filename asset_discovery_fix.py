from __future__ import annotations
import asyncio
import logging
import os
import re

log = logging.getLogger("candice.assets")
ASSET_EVENTS = tuple(int(x.strip()) for x in os.getenv("OLYMPTRADE_ASSET_EVENTS", "72,75,126,141,2076,2223,2301").split(",") if x.strip().isdigit())
FLEX_ONLY = os.getenv("OLYMPTRADE_FLEX_ONLY", "1").strip().lower() not in {"0", "false", "no", "off"}
_PAIR_RE = re.compile(r"^[A-Z0-9][A-Z0-9_./-]{2,29}$")
_ASSET_KEYS = {"pair", "symbol", "asset", "instrument", "code", "ticker", "short_name", "display_name", "symbol_name", "pair_name"}
_PROFIT_KEYS = {"profitability", "profitability_percent", "profitability_percentage", "max_profitability", "max_profitability_percent", "payout", "payout_percent", "profit", "profit_percent"}


def _candidate(value):
    if not isinstance(value, str):
        return None
    s = value.strip().upper()
    if s in {"FLEX", "FLEX TIME", "FLEX_TIME", "FIXED TIME", "FOREX", "STOCKS"}:
        return None
    if _PAIR_RE.fullmatch(s) and any(ch.isalpha() for ch in s):
        return s
    return None


def _is_flex_mode(value):
    if not isinstance(value, str):
        return False
    normalized = re.sub(r"[\s_-]+", "", value.strip().lower())
    return normalized in {"flex", "flextime"}


def _has_profitability(node):
    if not isinstance(node, dict):
        return False
    for key, value in node.items():
        if str(key).lower() not in _PROFIT_KEYS:
            continue
        try:
            number = float(str(value).replace("%", "").strip())
            # Olymptrade may encode 79.4 as a percentage or 0.794 as a ratio.
            if 0 < number <= 100:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _has_flex_marker(node):
    if not isinstance(node, dict):
        return False
    # Accept only explicit Flex/Flex Time evidence or a profitability field
    # known to be exposed with Flex assets. Never infer Flex from the symbol.
    for key, value in node.items():
        k = str(key).lower()
        if k in {"mode", "trading_mode", "trade_mode", "type", "market_type", "market", "name"} and _is_flex_mode(value):
            return True
        if k in {"modes", "trading_modes", "trade_modes", "available_modes", "markets", "market_modes", "types"}:
            vals = value if isinstance(value, list) else [value]
            for item in vals:
                if isinstance(item, str) and _is_flex_mode(item):
                    return True
                if isinstance(item, dict) and _has_flex_marker(item):
                    return True
        if k in {"flex", "is_flex", "flex_enabled", "flex_available", "available_flex", "flex_time", "is_flex_time", "flextime"}:
            if value is True:
                return True
            if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes", "enabled", "available", "flex", "flex time", "flex_time", "flextime"}:
                return True
    return _has_profitability(node)


def _extract(node, found, flex_context=False, stats=None):
    if stats is None:
        stats = {"dicts": 0, "lists": 0, "candidates": 0, "qualified_nodes": 0}
    if isinstance(node, dict):
        stats["dicts"] += 1
        local_flex = flex_context or _has_flex_marker(node)
        if local_flex:
            stats["qualified_nodes"] += 1
        # Some payloads use the asset symbol as the dictionary key, with its
        # metadata (profitability/mode) stored in the value object.
        for raw_key, value in node.items():
            key_candidate = _candidate(raw_key)
            if key_candidate and isinstance(value, dict) and (_has_flex_marker(value) or local_flex):
                found.add(key_candidate)
                stats["candidates"] += 1
            k = str(raw_key).lower()
            if k in _ASSET_KEYS and local_flex:
                values = value if isinstance(value, list) else [value]
                for item in values:
                    if isinstance(item, dict):
                        for subkey in ("id", "pair", "symbol", "asset", "instrument", "p", "code", "ticker", "short_name", "display_name", "symbol_name", "pair_name"):
                            c = _candidate(item.get(subkey))
                            if c:
                                found.add(c)
                                stats["candidates"] += 1
                    else:
                        c = _candidate(item)
                        if c:
                            found.add(c)
                            stats["candidates"] += 1
            _extract(value, found, local_flex, stats)
    elif isinstance(node, list):
        stats["lists"] += 1
        for item in node:
            _extract(item, found, flex_context, stats)
    return stats


def apply():
    from market_feed import LiveMarketFeed
    if getattr(LiveMarketFeed, "_asset_discovery_hardened", False):
        return
    original_start = LiveMarketFeed.start
    original_stop = LiveMarketFeed.stop

    async def hardened_start(self):
        async def enhanced_assets(msg):
            found = set()
            stats = {"dicts": 0, "lists": 0, "candidates": 0, "qualified_nodes": 0}
            payload = msg.get("d") if isinstance(msg, dict) else msg
            _extract(payload, found, False, stats)
            if found:
                new = found - self.assets
                if new:
                    self.assets.update(new)
                    log.info("FLEX_TIME_ASSET_DISCOVERY found=%s new=%s total=%s", len(found), len(new), len(self.assets))
                    for asset in sorted(new):
                        if asset not in self.subscribed:
                            self.schedule_asset(asset)
            elif FLEX_ONLY and isinstance(msg, dict) and msg.get("e") in ASSET_EVENTS:
                log.info("FLEX_TIME_ASSET_SCAN event=%s dicts=%s lists=%s qualified_nodes=%s candidates=%s", msg.get("e"), stats["dicts"], stats["lists"], stats["qualified_nodes"], stats["candidates"])

        if FLEX_ONLY:
            self.assets.clear()
        self.client.on("*", enhanced_assets)
        await original_start(self)

        async def request_discovery_events():
            if not self.client.running:
                return
            events = list(dict.fromkeys((220,) + ASSET_EVENTS))
            log.info("FLEX_TIME_ASSET_EVENT_REQUESTS events=%s", events)
            for event_id in events:
                try:
                    await self.client.send(98, [event_id], False)
                except Exception as exc:
                    log.debug("FLEX_TIME_ASSET_EVENT_REQUEST_FAILED event=%s error=%s", event_id, type(exc).__name__)

        self._asset_discovery_request_task = asyncio.create_task(request_discovery_events())

        async def discovery_watch():
            while self.connected:
                try:
                    await asyncio.sleep(30)
                    if self.connected:
                        await request_discovery_events()
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    log.debug("ASSET_DISCOVERY_REFRESH_FAILED error=%s", type(exc).__name__)
                    await asyncio.sleep(30)
        self._asset_discovery_task = asyncio.create_task(discovery_watch())
        log.info("FLEX_TIME_ASSET_DISCOVERY_WATCH_STARTED events=%s flex_only=%s", ASSET_EVENTS, FLEX_ONLY)

    async def hardened_stop(self):
        for name in ("_asset_discovery_task", "_asset_discovery_request_task"):
            task = getattr(self, name, None)
            if task and not task.done():
                task.cancel()
        await original_stop(self)

    LiveMarketFeed.start = hardened_start
    LiveMarketFeed.stop = hardened_stop
    LiveMarketFeed._asset_discovery_hardened = True
    log.info("FLEX_TIME_ASSET_DISCOVERY_HARDENED flex_only=%s", FLEX_ONLY)

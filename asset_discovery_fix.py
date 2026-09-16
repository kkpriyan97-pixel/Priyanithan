from __future__ import annotations
import asyncio
import logging
import os
import re

log = logging.getLogger("candice.assets")
ASSET_EVENTS = tuple(int(x.strip()) for x in os.getenv("OLYMPTRADE_ASSET_EVENTS", "72,75,126,141,2076,2223,2301").split(",") if x.strip().isdigit())
FLEX_ONLY = os.getenv("OLYMPTRADE_FLEX_ONLY", "1").strip().lower() not in {"0","false","no","off"}
_PAIR_RE = re.compile(r"^[A-Z0-9][A-Z0-9_./-]{2,29}$")


def _candidate(value):
    if not isinstance(value, str):
        return None
    s = value.strip().upper()
    if _PAIR_RE.fullmatch(s) and any(ch.isalpha() for ch in s):
        return s
    return None


def _is_flex_mode(value):
    if not isinstance(value, str):
        return False
    normalized = re.sub(r"[\s_-]+", "", value.strip().lower())
    return normalized in {"flex", "flextime"}


def _has_flex_marker(node):
    if not isinstance(node, dict):
        return False
    # Accept only explicit Flex/Flex Time evidence from the market payload.
    # OlympTrade's Flex asset list exposes profitability, while the UI mode
    # may be encoded as Flex, Flex Time, flex_time, or a boolean flag.
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
        # Flex asset rows shown in the app carry a profitability value.
        if k in {"profitability", "profitability_percent", "profitability_percentage", "max_profitability", "max_profitability_percent", "payout", "payout_percent"}:
            try:
                number = float(str(value).replace("%", "").strip())
                if 0 < number <= 100:
                    return True
            except (TypeError, ValueError):
                pass
    return False


def _extract(node, found, flex_context=False):
    if isinstance(node, dict):
        local_flex = flex_context or _has_flex_marker(node)
        for key, value in node.items():
            k = str(key).lower()
            if k in {"pair", "symbol", "asset", "instrument", "code"} and local_flex:
                values = value if isinstance(value, list) else [value]
                for item in values:
                    if isinstance(item, dict):
                        for subkey in ("id", "pair", "symbol", "asset", "instrument", "p", "code"):
                            c = _candidate(item.get(subkey))
                            if c:
                                found.add(c)
                    else:
                        c = _candidate(item)
                        if c:
                            found.add(c)
            _extract(value, found, local_flex)
    elif isinstance(node, list):
        for item in node:
            _extract(item, found, flex_context)


def apply():
    from market_feed import LiveMarketFeed
    if getattr(LiveMarketFeed, "_asset_discovery_hardened", False):
        return
    original_start = LiveMarketFeed.start
    original_stop = LiveMarketFeed.stop

    async def hardened_start(self):
        async def enhanced_assets(msg):
            found = set()
            _extract(msg.get("d") if isinstance(msg, dict) else msg, found, False)
            new = found - self.assets
            if new:
                self.assets.update(new)
                log.info("FLEX_TIME_ASSET_DISCOVERY found=%s new=%s total=%s", len(found), len(new), len(self.assets))
                for asset in sorted(new):
                    if asset not in self.subscribed:
                        self.schedule_asset(asset)
            elif FLEX_ONLY and isinstance(msg, dict):
                log.debug("FLEX_TIME_ASSET_DISCOVERY no_explicit_flex_time_assets event=%s", msg.get("e"))

        if FLEX_ONLY:
            self.assets.clear()
        self.client.on("*", enhanced_assets)
        await original_start(self)

        async def request_discovery_events():
            if not self.client.running:
                return
            # 220 is the primary asset-list request. The additional known
            # asset events are also refreshed so Flex Time rows are exposed
            # even when the server sends them on a secondary event channel.
            events = list(dict.fromkeys((220,) + ASSET_EVENTS))
            for event_id in events:
                try:
                    await self.client.send(98, [event_id], False)
                except Exception as exc:
                    log.debug("FLEX_TIME_ASSET_EVENT_REQUEST_FAILED event=%s error=%s", event_id, type(exc).__name__)

        await request_discovery_events()

        async def discovery_watch():
            while self.connected:
                try:
                    await request_discovery_events()
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    log.debug("ASSET_DISCOVERY_REFRESH_FAILED error=%s", type(exc).__name__)
                    await asyncio.sleep(30)
        self._asset_discovery_task = asyncio.create_task(discovery_watch())
        log.info("FLEX_TIME_ASSET_DISCOVERY_WATCH_STARTED events=%s flex_only=%s", ASSET_EVENTS, FLEX_ONLY)

    async def hardened_stop(self):
        task = getattr(self, "_asset_discovery_task", None)
        if task and not task.done():
            task.cancel()
        await original_stop(self)

    LiveMarketFeed.start = hardened_start
    LiveMarketFeed.stop = hardened_stop
    LiveMarketFeed._asset_discovery_hardened = True
    log.info("FLEX_TIME_ASSET_DISCOVERY_HARDENED flex_only=%s", FLEX_ONLY)

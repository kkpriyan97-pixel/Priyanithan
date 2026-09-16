from __future__ import annotations

import asyncio
import logging
import os
import re

log = logging.getLogger("candice.assets")

# The OlympTrade stream has changed event layouts over time.  Listen to the
# market/instrument events that can carry pair metadata instead of assuming a
# single event id.  This remains read-only: it only discovers and subscribes
# to market-data streams.
ASSET_EVENTS = tuple(
    int(x.strip())
    for x in os.getenv(
        "OLYMPTRADE_ASSET_EVENTS",
        "72,75,126,141,2076,2223,2301",
    ).split(",")
    if x.strip().isdigit()
)

_PAIR_RE = re.compile(r"^[A-Z0-9][A-Z0-9_./-]{2,29}$")


def _candidate(value):
    if isinstance(value, str):
        s = value.strip().upper()
        if _PAIR_RE.fullmatch(s) and any(ch.isalpha() for ch in s):
            return s
    return None


def _extract(node, found):
    if isinstance(node, dict):
        for key, value in node.items():
            k = str(key).lower()
            if k in {"pair", "symbol", "asset", "instrument", "p"}:
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
            _extract(value, found)
    elif isinstance(node, list):
        for item in node:
            _extract(item, found)


def apply():
    """Install runtime asset-discovery hardening before the feed starts."""
    from market_feed import LiveMarketFeed

    if getattr(LiveMarketFeed, "_asset_discovery_hardened", False):
        return

    original_start = LiveMarketFeed.start
    original_assets = LiveMarketFeed._assets

    async def enhanced_assets(self, msg):
        found = set()
        payload = msg.get("d") if isinstance(msg, dict) else msg
        _extract(payload, found)

        # Preserve the existing parser as a compatibility fallback.
        try:
            await original_assets(self, msg)
        except Exception:
            log.exception("ASSET_DISCOVERY_LEGACY_HANDLER_FAILED")

        new = found - self.assets
        for asset in sorted(new):
            await self._subscribe_asset(asset)
        if found:
            self.assets.update(found)
            log.info("ASSET_DISCOVERY found=%s total=%s", len(found), len(self.assets))

    async def hardened_start(self):
        # Register several known market/instrument event families before the
        # connection is initialized so the first asset-list response is not
        # missed.
        for event in ASSET_EVENTS:
            self.client.on(event, enhanced_assets.__get__(self, LiveMarketFeed))

        # Keep the configured assets if present; never replace them with a
        # single hard-coded symbol.
        await original_start(self)

        async def discovery_watch():
            while self.connected:
                try:
                    if self.client.running:
                        # Re-request the market metadata subscription at a low
                        # rate.  This is market-data discovery only, not trading.
                        await self.client.send(98, [220], False)
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    log.debug("ASSET_DISCOVERY_REFRESH_FAILED error=%s", type(exc).__name__)
                    await asyncio.sleep(30)

        self._asset_discovery_task = asyncio.create_task(discovery_watch())
        log.info("ASSET_DISCOVERY_WATCH_STARTED events=%s", ASSET_EVENTS)

    original_stop = LiveMarketFeed.stop

    async def hardened_stop(self):
        task = getattr(self, "_asset_discovery_task", None)
        if task and not task.done():
            task.cancel()
        await original_stop(self)

    LiveMarketFeed.start = hardened_start
    LiveMarketFeed.stop = hardened_stop
    LiveMarketFeed._asset_discovery_hardened = True
    log.info("ASSET_DISCOVERY_HARDENED")

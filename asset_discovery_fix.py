from __future__ import annotations
import asyncio, logging, os

log = logging.getLogger("candice.assets")
FLEX_ONLY = os.getenv("OLYMPTRADE_FLEX_ONLY", "1").strip().lower() not in {"0", "false", "no", "off"}

async def _flex_refresh_loop(feed):
    while getattr(feed, "client", None) is not None and getattr(feed.client, "running", False):
        try:
            # 183 is the only accepted account/Flex asset channel. No public/global fallback.
            await feed.client.send(98, [183], False)
            log.debug("FLEX_ASSET_REFRESH_REQUEST event=183")
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.debug("FLEX_ASSET_REFRESH_FAILED error=%s", type(e).__name__)
        await asyncio.sleep(30)

def apply():
    from market_feed import LiveMarketFeed
    if getattr(LiveMarketFeed, "_asset_discovery_hardened", False):
        return
    original_start = LiveMarketFeed.start
    original_stop = LiveMarketFeed.stop

    async def hardened_start(self):
        if FLEX_ONLY:
            self.assets.clear()
            self.subscribed.clear()
        await original_start(self)
        if FLEX_ONLY and getattr(self, "client", None):
            task = asyncio.create_task(_flex_refresh_loop(self))
            self._flex_refresh_task = task
        log.info("ACCOUNT_ASSET_DISCOVERY_DISABLED_GENERIC_SOURCE=true authenticated_flex_channel=event_183 strict_fail_closed=true")

    async def hardened_stop(self):
        task = getattr(self, "_flex_refresh_task", None)
        if task and not task.done():
            task.cancel()
        await original_stop(self)

    LiveMarketFeed.start = hardened_start
    LiveMarketFeed.stop = hardened_stop
    LiveMarketFeed._asset_discovery_hardened = True
    log.info("ACCOUNT_ASSET_DISCOVERY_HARDENED source=authenticated_flex_event_183 strict_fail_closed=true")

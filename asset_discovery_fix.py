from __future__ import annotations
import logging, os
log=logging.getLogger("candice.assets")
FLEX_ONLY=os.getenv("OLYMPTRADE_FLEX_ONLY","1").strip().lower() not in {"0","false","no","off"}
def apply():
    from market_feed import LiveMarketFeed
    if getattr(LiveMarketFeed,"_asset_discovery_hardened",False): return
    original_start=LiveMarketFeed.start
    original_stop=LiveMarketFeed.stop
    async def hardened_start(self):
        if FLEX_ONLY:
            self.assets.clear()
            self.subscribed.clear()
        await original_start(self)
        log.info("ACCOUNT_ASSET_DISCOVERY_DISABLED_GENERIC_SOURCE=true authenticated_flex_channel=sitecustomize_event_183")
    async def hardened_stop(self):
        await original_stop(self)
    LiveMarketFeed.start=hardened_start
    LiveMarketFeed.stop=hardened_stop
    LiveMarketFeed._asset_discovery_hardened=True
    log.info("ACCOUNT_ASSET_DISCOVERY_HARDENED source=authenticated_flex_event_183 strict_fail_closed=true")

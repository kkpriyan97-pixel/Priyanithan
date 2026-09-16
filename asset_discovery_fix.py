from __future__ import annotations
import asyncio
import logging
import os
import re

log = logging.getLogger("candice.assets")
ASSET_EVENTS = tuple(int(x.strip()) for x in os.getenv("OLYMPTRADE_ASSET_EVENTS", "72,75,126,141,2076,2223,2301").split(",") if x.strip().isdigit())
_PAIR_RE = re.compile(r"^[A-Z0-9][A-Z0-9_./-]{2,29}$")

def _candidate(value):
    if not isinstance(value, str): return None
    s=value.strip().upper()
    if _PAIR_RE.fullmatch(s) and any(ch.isalpha() for ch in s): return s
    return None

def _extract(node, found):
    if isinstance(node, dict):
        for key,value in node.items():
            k=str(key).lower()
            if k in {"pair","symbol","asset","instrument","code"}:
                values=value if isinstance(value,list) else [value]
                for item in values:
                    if isinstance(item,dict):
                        for subkey in ("id","pair","symbol","asset","instrument","p","code"):
                            c=_candidate(item.get(subkey))
                            if c: found.add(c)
                    else:
                        c=_candidate(item)
                        if c: found.add(c)
            _extract(value,found)
    elif isinstance(node,list):
        for item in node:_extract(item,found)

def apply():
    from market_feed import LiveMarketFeed
    if getattr(LiveMarketFeed,"_asset_discovery_hardened",False): return
    original_start=LiveMarketFeed.start
    original_stop=LiveMarketFeed.stop

    async def enhanced_assets(self,msg):
        found=set()
        _extract(msg.get("d") if isinstance(msg,dict) else msg,found)
        new=found-self.assets
        if new:
            self.assets.update(new)
            log.info("ASSET_DISCOVERY found=%s new=%s total=%s assets=%s",len(found),len(new),len(self.assets),sorted(found))
            for asset in sorted(new):
                if asset not in self.subscribed:
                    asyncio.create_task(self._subscribe_asset(asset))

    async def hardened_start(self):
        # Listen broadly for metadata-bearing responses. Discovery never waits
        # inside the WebSocket dispatcher; each new subscription is a task.
        self.client.on("*", enhanced_assets)
        for event in ASSET_EVENTS:
            self.client.on(event, enhanced_assets)
        await original_start(self)

        async def discovery_watch():
            while self.connected:
                try:
                    if self.client.running:
                        await self.client.send(98,[220],False)
                    await asyncio.sleep(30)
                except asyncio.CancelledError: return
                except Exception as exc:
                    log.debug("ASSET_DISCOVERY_REFRESH_FAILED error=%s",type(exc).__name__)
                    await asyncio.sleep(30)
        self._asset_discovery_task=asyncio.create_task(discovery_watch())
        log.info("ASSET_DISCOVERY_WATCH_STARTED events=%s",ASSET_EVENTS)

    async def hardened_stop(self):
        task=getattr(self,"_asset_discovery_task",None)
        if task and not task.done(): task.cancel()
        await original_stop(self)

    LiveMarketFeed.start=hardened_start
    LiveMarketFeed.stop=hardened_stop
    LiveMarketFeed._asset_discovery_hardened=True
    log.info("ASSET_DISCOVERY_HARDENED")

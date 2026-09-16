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
    if not isinstance(value, str): return None
    s=value.strip().upper()
    if _PAIR_RE.fullmatch(s) and any(ch.isalpha() for ch in s): return s
    return None

def _has_flex_marker(node):
    if not isinstance(node, dict): return False
    # OlympTrade payloads can represent supported modes in different fields.
    # We only accept an asset in FLEX_ONLY mode when its payload explicitly
    # identifies Flex/flex as a supported mode. We do not guess from symbol.
    for key, value in node.items():
        k=str(key).lower()
        if k in {"mode","trading_mode","trade_mode","type","market_type"} and isinstance(value,str) and value.strip().lower()=="flex":
            return True
        if k in {"modes","trading_modes","trade_modes","available_modes","markets","market_modes","types"}:
            vals=value if isinstance(value,list) else [value]
            for item in vals:
                if isinstance(item,str) and item.strip().lower()=="flex": return True
                if isinstance(item,dict):
                    if _has_flex_marker(item): return True
        if k in {"flex","is_flex","flex_enabled","flex_available","available_flex"}:
            if value is True: return True
            if isinstance(value,str) and value.strip().lower() in {"true","1","yes","enabled","available","flex"}: return True
    return False

def _extract(node, found, flex_context=False):
    if isinstance(node, dict):
        local_flex=flex_context or _has_flex_marker(node)
        for key,value in node.items():
            k=str(key).lower()
            if k in {"pair","symbol","asset","instrument","code"} and local_flex:
                values=value if isinstance(value,list) else [value]
                for item in values:
                    if isinstance(item,dict):
                        for subkey in ("id","pair","symbol","asset","instrument","p","code"):
                            c=_candidate(item.get(subkey))
                            if c: found.add(c)
                    else:
                        c=_candidate(item)
                        if c: found.add(c)
            _extract(value,found,local_flex)
    elif isinstance(node,list):
        for item in node:_extract(item,found,flex_context)

def apply():
    from market_feed import LiveMarketFeed
    if getattr(LiveMarketFeed,"_asset_discovery_hardened",False): return
    original_start=LiveMarketFeed.start
    original_stop=LiveMarketFeed.stop

    async def hardened_start(self):
        async def enhanced_assets(msg):
            found=set()
            _extract(msg.get("d") if isinstance(msg,dict) else msg,found,False)
            new=found-self.assets
            if new:
                self.assets.update(new)
                log.info("FLEX_ASSET_DISCOVERY found=%s new=%s total=%s",len(found),len(new),len(self.assets))
                for asset in sorted(new):
                    if asset not in self.subscribed:
                        self.schedule_asset(asset)
            elif FLEX_ONLY and isinstance(msg,dict):
                log.debug("FLEX_ASSET_DISCOVERY no_explicit_flex_assets event=%s",msg.get("e"))

        # In FLEX_ONLY mode, do not seed arbitrary OLYMPTRADE_ASSETS values.
        if FLEX_ONLY:
            self.assets.clear()
        self.client.on("*", enhanced_assets)
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
        log.info("ASSET_DISCOVERY_WATCH_STARTED events=%s flex_only=%s",ASSET_EVENTS,FLEX_ONLY)

    async def hardened_stop(self):
        task=getattr(self,"_asset_discovery_task",None)
        if task and not task.done(): task.cancel()
        await original_stop(self)

    LiveMarketFeed.start=hardened_start
    LiveMarketFeed.stop=hardened_stop
    LiveMarketFeed._asset_discovery_hardened=True
    log.info("ASSET_DISCOVERY_HARDENED flex_only=%s",FLEX_ONLY)

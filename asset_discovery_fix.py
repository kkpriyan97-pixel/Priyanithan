from __future__ import annotations

import asyncio
import logging
import os
import re

log = logging.getLogger("candice.assets")
ACCOUNT_ASSET_EVENT = int(os.getenv("OLYMPTRADE_ACCOUNT_ASSET_EVENT", "220"))
FLEX_ONLY = os.getenv("OLYMPTRADE_FLEX_ONLY", "1").strip().lower() not in {"0", "false", "no", "off"}
_PAIR_RE = re.compile(r"^[A-Z0-9][A-Z0-9_./-]{2,29}$")
_ASSET_KEYS = {"pair", "symbol", "asset", "instrument", "code", "ticker", "short_name", "display_name", "symbol_name", "pair_name"}
_PROFIT_KEYS = {"profitability", "profitability_percent", "profitability_percentage", "max_profitability", "max_profitability_percent", "payout", "payout_percent", "profit", "profit_percent", "winperc", "win_percent", "win_percentage"}


def _candidate(value):
    if not isinstance(value, str): return None
    s = value.strip().upper()
    if s in {"FLEX", "FLEX TIME", "FLEX_TIME", "FIXED TIME", "FOREX", "STOCKS"}: return None
    return s if _PAIR_RE.fullmatch(s) and any(ch.isalpha() for ch in s) else None


def _is_flex_mode(value):
    if not isinstance(value, str): return False
    return re.sub(r"[\s_-]+", "", value.strip().lower()) in {"flex", "flextime"}


def _has_profitability(node):
    if not isinstance(node, dict): return False
    for key, value in node.items():
        if str(key).lower() in _PROFIT_KEYS:
            try:
                number = float(str(value).replace("%", "").strip())
                if 0 < number <= 100: return True
            except (TypeError, ValueError):
                pass
    return False


def _has_flex_marker(node):
    if not isinstance(node, dict): return False
    for key, value in node.items():
        k = str(key).lower()
        if k in {"mode", "trading_mode", "trade_mode", "type", "market_type", "market", "name"} and _is_flex_mode(value):
            return True
        if k in {"modes", "trading_modes", "trade_modes", "available_modes", "markets", "market_modes", "types"}:
            vals = value if isinstance(value, list) else [value]
            for item in vals:
                if isinstance(item, str) and _is_flex_mode(item): return True
                if isinstance(item, dict) and _has_flex_marker(item): return True
        if k in {"flex", "is_flex", "flex_enabled", "flex_available", "available_flex", "flex_time", "is_flex_time", "flextime"}:
            if value is True or (isinstance(value, str) and value.strip().lower() in {"true", "1", "yes", "enabled", "available", "flex", "flex time", "flex_time", "flextime"}):
                return True
    return _has_profitability(node)


def _extract(node, found, stats=None):
    """Extract symbols from one authoritative account asset-list payload.

    Qualification is record-scoped. No Flex/profitability marker is inherited
    from an ancestor into unrelated nested records.
    """
    if stats is None: stats = {"dicts": 0, "lists": 0, "candidates": 0, "qualified_nodes": 0}
    if isinstance(node, dict):
        stats["dicts"] += 1
        node_flex = _has_flex_marker(node)
        if node_flex: stats["qualified_nodes"] += 1
        for raw_key, value in node.items():
            key_candidate = _candidate(raw_key)
            if key_candidate and isinstance(value, dict) and _has_flex_marker(value):
                if key_candidate not in found:
                    found.add(key_candidate); stats["candidates"] += 1
        for raw_key, value in node.items():
            if str(raw_key).lower() not in _ASSET_KEYS: continue
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, dict):
                    if not (node_flex or _has_flex_marker(item)): continue
                    for subkey in ("id", "pair", "symbol", "asset", "instrument", "p", "code", "ticker", "short_name", "display_name", "symbol_name", "pair_name"):
                        c = _candidate(item.get(subkey))
                        if c:
                            if c not in found:
                                found.add(c); stats["candidates"] += 1
                            break
                elif node_flex:
                    c = _candidate(item)
                    if c:
                        if c not in found:
                            found.add(c); stats["candidates"] += 1
        for value in node.values(): _extract(value, found, stats)
    elif isinstance(node, list):
        stats["lists"] += 1
        for item in node: _extract(item, found, stats)
    return stats


def apply():
    from market_feed import LiveMarketFeed
    if getattr(LiveMarketFeed, "_asset_discovery_hardened", False): return
    original_start = LiveMarketFeed.start
    original_stop = LiveMarketFeed.stop

    async def hardened_start(self):
        if FLEX_ONLY:
            # Fail closed: do not retain assets from a previous process.
            self.assets.clear(); self.subscribed.clear()

        async def account_assets(msg):
            if not isinstance(msg, dict) or msg.get("e") != ACCOUNT_ASSET_EVENT: return
            found = set(); stats = {"dicts": 0, "lists": 0, "candidates": 0, "qualified_nodes": 0}
            payload = msg.get("d")
            _extract(payload, found, stats)
            if not found:
                samples = list(payload.keys())[:30] if isinstance(payload, dict) else []
                log.warning("ACCOUNT_ASSET_DISCOVERY_EMPTY event=%s dicts=%s lists=%s qualified_nodes=%s candidates=%s key_samples=%s", ACCOUNT_ASSET_EVENT, stats["dicts"], stats["lists"], stats["qualified_nodes"], stats["candidates"], samples)
                return
            new = found - self.assets
            if new:
                self.assets.update(new)
                log.info("ACCOUNT_ASSET_DISCOVERY event=%s found=%s new=%s total=%s", ACCOUNT_ASSET_EVENT, len(found), len(new), len(self.assets))
                for asset in sorted(new):
                    if asset not in self.subscribed: self.schedule_asset(asset)

        self.client.on("*", account_assets)
        await original_start(self)

        async def request_account_assets():
            if not self.client.running: return
            log.info("ACCOUNT_ASSET_EVENT_REQUEST event=%s", ACCOUNT_ASSET_EVENT)
            try:
                await self.client.send(98, [ACCOUNT_ASSET_EVENT], False)
            except Exception as exc:
                log.warning("ACCOUNT_ASSET_EVENT_REQUEST_FAILED event=%s error=%s", ACCOUNT_ASSET_EVENT, type(exc).__name__)

        self._asset_discovery_request_task = asyncio.create_task(request_account_assets())

        async def discovery_watch():
            while self.connected:
                try:
                    await asyncio.sleep(30)
                    if self.connected: await request_account_assets()
                except asyncio.CancelledError: return
                except Exception as exc:
                    log.debug("ACCOUNT_ASSET_REFRESH_FAILED error=%s", type(exc).__name__)
                    await asyncio.sleep(30)

        self._asset_discovery_task = asyncio.create_task(discovery_watch())
        log.info("ACCOUNT_ASSET_DISCOVERY_WATCH_STARTED event=%s flex_only=%s", ACCOUNT_ASSET_EVENT, FLEX_ONLY)

    async def hardened_stop(self):
        for name in ("_asset_discovery_task", "_asset_discovery_request_task"):
            task = getattr(self, name, None)
            if task and not task.done(): task.cancel()
        await original_stop(self)

    LiveMarketFeed.start = hardened_start
    LiveMarketFeed.stop = hardened_stop
    LiveMarketFeed._asset_discovery_hardened = True
    log.info("ACCOUNT_ASSET_DISCOVERY_HARDENED authoritative_event=%s strict_record_scope=%s", ACCOUNT_ASSET_EVENT, FLEX_ONLY)

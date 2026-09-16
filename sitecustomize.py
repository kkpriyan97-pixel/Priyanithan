from __future__ import annotations
import logging
import re

log = logging.getLogger("candice.flex_probe")
_PAIR_RE = re.compile(r"^[A-Z0-9][A-Z0-9_./-]{2,29}$")
_ASSET_KEYS = {
    "pair", "symbol", "asset", "instrument", "code", "ticker",
    "short_name", "display_name", "symbol_name", "pair_name"
}

def _candidate(v):
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    if s in {"FLEX", "FLEX TIME", "FLEX_TIME", "FIXED TIME", "FOREX", "STOCKS"}:
        return None
    if not _PAIR_RE.fullmatch(s) or not any(c.isalpha() for c in s):
        return None
    return s

def _extract(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            key_l = str(key).lower()
            if key_l in _ASSET_KEYS:
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

    async def on_message(msg):
        # HARD RULE: only authenticated Flex asset event 183 may define assets.
        if not isinstance(msg, dict) or msg.get("e") != 183:
            return

        found = set()
        _extract(msg.get("d"), found)
        if not found:
            log.warning("FLEX_ACCOUNT_ASSETS_EMPTY event=183")
            return

        authoritative = set(found)
        feed._authoritative_flex_assets = authoritative
        old = set(feed.assets)
        feed.assets = authoritative

        for asset in old - authoritative:
            feed.subscribed.discard(asset)
            task = feed._asset_tasks.pop(asset, None)
            if task and not task.done():
                task.cancel()

        log.info(
            "FLEX_ACCOUNT_ASSETS_AUTHORITATIVE event=183 demo_real=authenticated found=%s total=%s",
            sorted(authoritative), len(authoritative)
        )

        for asset in sorted(authoritative):
            if asset not in feed.subscribed:
                feed.schedule_asset(asset)

    # Register ONLY the exact event. Never use the wildcard dispatcher.
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
    log.info("FLEX_ACCOUNT_PROBE_PATCHED source=authenticated_websocket event_183_only=true demo_real_supported=true")

_patch()

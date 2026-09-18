from __future__ import annotations
import logging, re

log = logging.getLogger("candice.flex_probe")
_PAIR_RE = re.compile(r"^[A-Z0-9][A-Z0-9_./-]{2,39}$")
_ASSET_KEYS = {
    "pair", "symbol", "asset", "instrument", "ticker", "short_name",
    "display_name", "symbol_name", "pair_name", "asset_id", "asset_name",
    "asset_code", "symbol_code", "ticker_symbol", "instrument_name",
    "instrument_code", "shortName",
}
_BAD = {
    "FLEX", "FLEX TIME", "FLEX_TIME", "FIXED TIME", "FOREX", "STOCKS",
    "ASSET", "SYMBOL", "PAIR", "INSTRUMENT", "TICKER",
}

def _candidate(v):
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    if s in _BAD or " " in s or not _PAIR_RE.fullmatch(s) or not any(c.isalpha() for c in s):
        return None
    return s

def _market(asset):
    s = str(asset).upper()
    if re.search(r"(?:^|[_./-])OTC(?:$|[_./-])", s):
        return "OTC"
    if re.search(r"(?:^|[_./-])FLEX(?:$|[_./-])", s):
        return "FLEX"
    return None

def _extract(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            lk = str(key).strip().lower()
            if lk in {k.lower() for k in _ASSET_KEYS}:
                vals = value if isinstance(value, list) else [value]
                for item in vals:
                    if isinstance(item, dict):
                        for subkey in _ASSET_KEYS:
                            c = _candidate(item.get(subkey))
                            if c:
                                out.add(c)
                                break
                    else:
                        c = _candidate(item)
                        if c:
                            out.add(c)

            # An explicitly suffixed OTC/FLEX identifier is authoritative even
            # when the platform puts it under a generic id/code field.
            if isinstance(value, str):
                c = _candidate(value)
                if c and _market(c):
                    out.add(c)

            if isinstance(key, str) and isinstance(value, dict):
                c = _candidate(key)
                value_keys = {str(x).strip().lower() for x in value.keys()}
                if c and (bool(value_keys & {x.lower() for x in _ASSET_KEYS}) or _market(c)):
                    out.add(c)
                _extract(value, out)
            elif isinstance(value, (dict, list)):
                _extract(value, out)

    elif isinstance(node, list):
        for item in node:
            _extract(item, out)

def _publish(feed, found, source):
    found = {a for a in found if _candidate(a)}
    if not found:
        return
    previous = set(getattr(feed, "_flex_seen_assets", set()))
    new = found - previous
    if not new:
        return

    feed._flex_seen_assets.update(new)
    feed._authoritative_flex_assets = set(feed._flex_seen_assets)
    feed.assets = set(feed._flex_seen_assets)

    for asset in sorted(new):
        market = _market(asset)
        if market:
            feed.asset_market[asset] = market
            log.info(
                "ASSET_MARKET_EXPLICIT asset=%s market=%s source=%s",
                asset, market, source,
            )
        feed.schedule_asset(asset)

    log.info(
        "FLEX_ASSET_DISCOVERED source=%s new=%s total=%s assets=%s",
        source, len(new), len(feed.assets), sorted(new),
    )

def _attach(feed):
    if getattr(feed, "_resilient_asset_discovery_attached", False):
        return

    feed._flex_seen_assets = set(getattr(feed, "_flex_seen_assets", set()))
    feed._authoritative_flex_assets = set(
        getattr(feed, "_authoritative_flex_assets", set())
    )
    feed.asset_market = dict(getattr(feed, "asset_market", {}))

    async def on183(msg):
        if not isinstance(msg, dict):
            return
        found = set()
        _extract(msg.get("d"), found)
        log.info(
            "FLEX_EVENT_183_SHAPE top=%s found=%s",
            type(msg.get("d")).__name__, sorted(found),
        )
        if found:
            _publish(feed, found, "authenticated_event_183")

    # IMPORTANT: do not use a wildcard authenticated-event source here.
    # Other authenticated events (for example event 1097) contain broad
    # platform catalogues and are not the authoritative Flex asset universe.
    feed.client.on(183, on183)

    feed._resilient_asset_discovery_attached = True
    log.info(
        "FLEX_RESILIENT_DISCOVERY_ATTACHED "
        "authenticated_only=true event_183_only=true "
        "explicit_otc_detection=true market_type_inference=false"
    )

def _patch():
    try:
        from market_feed import LiveMarketFeed
    except Exception:
        return
    if getattr(LiveMarketFeed, "_resilient_asset_discovery_patched", False):
        return

    original = LiveMarketFeed.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        _attach(self)

    LiveMarketFeed.__init__ = init
    LiveMarketFeed._resilient_asset_discovery_patched = True
    log.info(
        "FLEX_RESILIENT_DISCOVERY_PATCHED "
        "authenticated_only=true event_183_only=true "
        "explicit_otc_detection=true market_type_inference=false"
    )

_patch()

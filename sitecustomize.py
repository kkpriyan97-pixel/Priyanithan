from __future__ import annotations
import logging,re
log=logging.getLogger("candice.flex_probe")
_PAIR_RE=re.compile(r"^[A-Z0-9][A-Z0-9_./-]{2,29}$")
_ASSET_KEYS={"pair","symbol","asset","instrument","code","ticker","short_name","display_name","symbol_name","pair_name","id","p"}

def _candidate(v):
    if not isinstance(v,str): return None
    s=v.strip().upper()
    if s in {"FLEX","FLEX TIME","FLEX_TIME","FIXED TIME","FOREX","STOCKS"}: return None
    return s if _PAIR_RE.fullmatch(s) and any(c.isalpha() for c in s) else None

def _extract(n,out):
    if isinstance(n,dict):
        for k,v in n.items():
            if str(k).lower() in _ASSET_KEYS:
                vals=v if isinstance(v,list) else [v]
                for item in vals:
                    if isinstance(item,dict):
                        for sk in ("pair","symbol","asset","instrument","code","ticker","short_name","display_name","symbol_name","pair_name","id","p"):
                            c=_candidate(item.get(sk))
                            if c: out.add(c); break
                    else:
                        c=_candidate(item)
                        if c: out.add(c)
            elif isinstance(v,(dict,list)):
                _extract(v,out)
    elif isinstance(n,list):
        for x in n: _extract(x,out)

def _attach(feed):
    if getattr(feed,"_account_flex_probe_attached",False): return
    async def on_message(msg):
        # HARD RULE: no asset discovery from any event except authenticated Flex event 183.
        if not isinstance(msg,dict) or msg.get("e") != 183: return
        found=set(); _extract(msg.get("d"),found)
        if not found:
            log.warning("FLEX_ACCOUNT_ASSETS_EMPTY event=183")
            return
        old=set(feed.assets); feed.assets=set(found)
        for asset in old-found:
            feed.subscribed.discard(asset)
            task=feed._asset_tasks.pop(asset,None)
            if task and not task.done(): task.cancel()
        log.info("FLEX_ACCOUNT_ASSETS_AUTHORITATIVE event=183 found=%s total=%s",sorted(found),len(found))
        for asset in sorted(found):
            if asset not in feed.subscribed: feed.schedule_asset(asset)
    feed.client.on(183,on_message)
    feed._account_flex_probe_attached=True

def _patch():
    try:
        from market_feed import LiveMarketFeed
    except Exception: return
    if getattr(LiveMarketFeed,"_account_flex_probe_patched",False): return
    original=LiveMarketFeed.__init__
    def init(self,*args,**kwargs):
        original(self,*args,**kwargs); _attach(self)
    LiveMarketFeed.__init__=init
    LiveMarketFeed._account_flex_probe_patched=True
    log.info("FLEX_ACCOUNT_PROBE_PATCHED source=authenticated_websocket event_183_only=true")
_patch()

from __future__ import annotations
import logging,re
log=logging.getLogger("candice.flex_probe")
_PAIR_RE=re.compile(r"^[A-Z0-9][A-Z0-9_./-]{2,29}$")
_ASSET_KEYS={"pair","symbol","asset","instrument","code","ticker","short_name","display_name","symbol_name","pair_name","id","p"}
_MODE_KEYS={"mode","trading_mode","trade_mode","market_type","market","type","name"}
_MODES_KEYS={"modes","trading_modes","trade_modes","available_modes","markets","market_modes","types"}
_FLEX_KEYS={"flex","is_flex","flex_enabled","flex_available","available_flex","flex_time","is_flex_time","flextime"}

def _candidate(v):
    if not isinstance(v,str): return None
    s=v.strip().upper()
    if s in {"FLEX","FLEX TIME","FLEX_TIME","FIXED TIME","FOREX","STOCKS"}: return None
    return s if _PAIR_RE.fullmatch(s) and any(c.isalpha() for c in s) else None

def _is_flex(v):
    return isinstance(v,str) and re.sub(r"[\s_-]+","",v.strip().lower()) in {"flex","flextime"}

def _has_explicit_flex(n):
    if not isinstance(n,dict): return False
    for k,v in n.items():
        kl=str(k).lower()
        if kl in _MODE_KEYS and _is_flex(v): return True
        if kl in _MODES_KEYS:
            vals=v if isinstance(v,list) else [v]
            if any(_is_flex(x) or (isinstance(x,dict) and _has_explicit_flex(x)) for x in vals): return True
        if kl in _FLEX_KEYS and (v is True or (isinstance(v,str) and v.strip().lower() in {"true","1","yes","enabled","available","flex","flex time","flex_time","flextime"})): return True
    return False

def _extract(n,out,force=False):
    if isinstance(n,dict):
        local_flex=_has_explicit_flex(n) or force
        for k,v in n.items():
            if str(k).lower() not in _ASSET_KEYS: continue
            vals=v if isinstance(v,list) else [v]
            for item in vals:
                if isinstance(item,dict):
                    if local_flex or _has_explicit_flex(item):
                        for sk in ("pair","symbol","asset","instrument","code","ticker","short_name","display_name","symbol_name","pair_name","id","p"):
                            c=_candidate(item.get(sk))
                            if c: out.add(c); break
                elif local_flex:
                    c=_candidate(item)
                    if c: out.add(c)
        for v in n.values(): _extract(v,out,force)
    elif isinstance(n,list):
        for x in n: _extract(x,out,force)

def _attach(feed):
    if getattr(feed,"_account_flex_probe_attached",False): return
    async def on_message(msg):
        if not isinstance(msg,dict) or msg.get("e") != 183: return
        found=set(); _extract(msg.get("d"),found,force=False)
        if not found:
            log.warning("FLEX_ACCOUNT_ASSETS_EMPTY event=183")
            return
        old=set(feed.assets); removed=old-found
        feed.assets=set(found)
        for asset in removed:
            feed.subscribed.discard(asset)
            task=feed._asset_tasks.pop(asset,None)
            if task and not task.done(): task.cancel()
        log.info("FLEX_ACCOUNT_ASSETS_AUTHORITATIVE event=183 found=%s total=%s",sorted(found),len(found))
        for asset in sorted(found):
            if asset not in feed.subscribed: feed.schedule_asset(asset)
    feed.client.on(183,on_message)
    feed._account_flex_probe_attached=True

_original_init=None
def _patch():
    global _original_init
    try:
        from market_feed import LiveMarketFeed
    except Exception:
        return
    if getattr(LiveMarketFeed,"_account_flex_probe_patched",False): return
    _original_init=LiveMarketFeed.__init__
    def init(self,*args,**kwargs):
        _original_init(self,*args,**kwargs)
        _attach(self)
    LiveMarketFeed.__init__=init
    LiveMarketFeed._account_flex_probe_patched=True
    log.info("FLEX_ACCOUNT_PROBE_PATCHED source=authenticated_websocket event_183_only=true")
_patch()

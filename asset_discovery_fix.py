from __future__ import annotations
import asyncio, logging, os, re
log=logging.getLogger("candice.assets")
FLEX_ONLY=os.getenv("OLYMPTRADE_FLEX_ONLY","1").strip().lower() not in {"0","false","no","off"}
_PAIR_RE=re.compile(r"^[A-Z0-9][A-Z0-9_./-]{2,29}$")
_ASSET_KEYS={"pair","symbol","asset","instrument","code","ticker","short_name","display_name","symbol_name","pair_name"}
_PROFIT_KEYS={"profitability","profitability_percent","profitability_percentage","max_profitability","max_profitability_percent","payout","payout_percent","profit","profit_percent","winperc","win_percent","win_percentage"}
def _candidate(value):
 if not isinstance(value,str): return None
 s=value.strip().upper()
 if s in {"FLEX","FLEX TIME","FLEX_TIME","FIXED TIME","FOREX","STOCKS","DIGITAL"}: return None
 return s if _PAIR_RE.fullmatch(s) and any(ch.isalpha() for ch in s) else None
def _has_profitability(node):
 if not isinstance(node,dict): return False
 return any(str(key).lower() in _PROFIT_KEYS for key in node)
def _extract(node,found,stats=None):
 if stats is None:stats={"dicts":0,"lists":0,"candidates":0,"qualified_nodes":0,"strings":0}
 if isinstance(node,dict):
  stats["dicts"]+=1
  node_ok=_has_profitability(node)
  if node_ok:stats["qualified_nodes"]+=1
  for raw_key,value in node.items():
   key_candidate=_candidate(raw_key)
   if key_candidate and isinstance(value,dict) and _has_profitability(value):
    found.add(key_candidate);stats["candidates"]+=1
  for raw_key,value in node.items():
   if str(raw_key).lower() not in _ASSET_KEYS:continue
   values=value if isinstance(value,list) else [value]
   for item in values:
    if isinstance(item,dict) and _has_profitability(item):
     for subkey in ("id","pair","symbol","asset","instrument","p","code","ticker","short_name","display_name","symbol_name","pair_name"):
      c=_candidate(item.get(subkey))
      if c:found.add(c);stats["candidates"]+=1;break
    elif node_ok:
     c=_candidate(item)
     if c:found.add(c)
  for value in node.values():_extract(value,found,stats)
 elif isinstance(node,list):
  stats["lists"]+=1
  for item in node:_extract(item,found,stats)
 elif isinstance(node,str):stats["strings"]+=1
 return stats
def _apply_found(feed,found,source):
 if not found:return False
 old=set(feed.assets);removed=old-found
 for asset in removed:
  feed.subscribed.discard(asset)
  task=feed._asset_tasks.pop(asset,None)
  if task and not task.done():task.cancel()
 feed.assets.intersection_update(found);feed.assets.update(found)
 new=found-old
 log.info("ACCOUNT_ASSET_DISCOVERY source=%s account_scoped=true flex_only=%s found=%s new=%s removed=%s total=%s",source,FLEX_ONLY,len(found),len(new),len(removed),len(feed.assets))
 for asset in sorted(new):
  if asset not in feed.subscribed:feed.schedule_asset(asset)
 return True
def apply():
 from market_feed import LiveMarketFeed
 if getattr(LiveMarketFeed,"_asset_discovery_hardened",False):return
 original_start=LiveMarketFeed.start;original_stop=LiveMarketFeed.stop
 async def hardened_start(self):
  if FLEX_ONLY:self.assets.clear();self.subscribed.clear()
  await original_start(self)
  async def account_asset_watch():
   last=set()
   while self.connected:
    try:
     rows=getattr(self.client,"account_profitability",[]) or []
     found=set();stats={"dicts":0,"lists":0,"candidates":0,"qualified_nodes":0,"strings":0}
     _extract(rows,found,stats)
     if found:
      _apply_found(self,found,"authenticated_account_event_182")
      last=set(found)
     elif last:
      self.assets.clear();self.subscribed.clear();last=set();log.warning("ACCOUNT_ASSET_DISCOVERY_EMPTY source=authenticated_account_event_182 fail_closed=true")
     elif stats["dicts"] or stats["lists"]:
      log.info("ACCOUNT_ASSET_DISCOVERY_WAITING source=authenticated_account_event_182 dicts=%s lists=%s candidates=%s",stats["dicts"],stats["lists"],stats["candidates"])
     await asyncio.sleep(5)
    except asyncio.CancelledError:return
    except Exception as exc:
     log.warning("ACCOUNT_ASSET_DISCOVERY_WATCH_FAILED error=%s",type(exc).__name__);await asyncio.sleep(5)
  self._asset_discovery_task=asyncio.create_task(account_asset_watch())
  log.info("ACCOUNT_ASSET_DISCOVERY_WATCH_STARTED source=authenticated_account_event_182 flex_only=%s",FLEX_ONLY)
 async def hardened_stop(self):
  task=getattr(self,"_asset_discovery_task",None)
  if task and not task.done():task.cancel()
  await original_stop(self)
 LiveMarketFeed.start=hardened_start;LiveMarketFeed.stop=hardened_stop;LiveMarketFeed._asset_discovery_hardened=True
 log.info("ACCOUNT_ASSET_DISCOVERY_HARDENED source=authenticated_event_182 account_scoped=true strict_fail_closed=true")

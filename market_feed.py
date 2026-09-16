from __future__ import annotations
import asyncio, logging, os, time
from collections import defaultdict, deque
from olymp_client import OlympReadOnlyClient

log = logging.getLogger("candice.feed")


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize(x, asset=""):
    if not isinstance(x, dict):
        return None
    a = str(x.get("p", x.get("pair", x.get("symbol", asset)))).upper().strip()
    o = num(x.get("open", x.get("o")))
    h = num(x.get("high", x.get("h")))
    l = num(x.get("low", x.get("l")))
    c = num(x.get("close", x.get("c")))
    t = num(x.get("t", x.get("timestamp", x.get("time"))))
    if None in (o, h, l, c, t) or not a:
        return None
    if t > 1e10:
        t /= 1000
    return a, {"open": o, "high": h, "low": l, "close": c, "timestamp": t}


def walk_candles(x):
    if isinstance(x, list):
        out = []
        for y in x:
            out.extend(walk_candles(y))
        return out
    if not isinstance(x, dict):
        return []
    if isinstance(x.get("candles"), list):
        return walk_candles(x["candles"])
    for k in ("d", "data", "items", "result"):
        if isinstance(x.get(k), (list, dict)):
            r = walk_candles(x[k])
            if r:
                return r
    return [x]


class LiveMarketFeed:
    def __init__(self, on_candle):
        self.on_candle = on_candle
        self.token = os.getenv("OLYMPTRADE_ACCESS_TOKEN", "").strip()
        self.client = OlympReadOnlyClient(self.token)
        self.assets = {a.strip().upper() for a in os.getenv("OLYMPTRADE_ASSETS", "").split(",") if a.strip()}
        self.subscribed = set()
        self.forming = {}
        self.history = defaultdict(lambda: deque(maxlen=360))
        self.last_completed = defaultdict(float)
        self.ticks = 0
        self.candles = 0
        self.connected = False

    async def start(self):
        if not self.token:
            raise RuntimeError("OLYMPTRADE_ACCESS_TOKEN is required")
        self.client.on(1, self._tick)
        self.client.on(1003, self._candle_response)
        self.client.on(55, self._account)
        self.client.on(72, self._assets)
        await self.client.connect()
        self.connected = True
        await self.client.initialize_read_only()
        await self._discover_and_seed()
        asyncio.create_task(self._poll_loop())
        log.info("LIVE_MARKET_FEED started | 1m candles | READ_ONLY")

    async def stop(self):
        self.connected = False
        await self.client.close()

    async def _subscribe_asset(self, asset):
        if asset in self.subscribed:
            return
        try:
            await self.client.subscribe_ticks(asset)
            response = await self.client.request_candles(asset, 360)
            self._consume_history(asset, response)
            self.subscribed.add(asset)
            self.assets.add(asset)
            log.info("ASSET_SUBSCRIBED asset=%s history=%s", asset, len(self.history[asset]))
        except Exception as e:
            log.warning("ASSET_SUBSCRIBE_FAILED asset=%s error=%s", asset, type(e).__name__)

    async def _discover_and_seed(self):
        if not self.assets:
            self.assets.add("ASIA_X")
        for asset in list(self.assets):
            await self._subscribe_asset(asset)
        log.info("ASSETS_READY count=%s", len(self.assets))

    def _account(self, msg):
        d = msg.get("d") if isinstance(msg, dict) else None
        if not isinstance(d, list):
            return
        demos, reals = [], []
        for x in d:
            if not isinstance(x, dict):
                continue
            group = str(x.get("group", "")).lower()
            balance = num(x.get("amount", x.get("amount_real", x.get("amount_free"))))
            if balance is None:
                continue
            item = (balance, str(x.get("currency", "") or ""))
            if group == "demo":
                demos.append(item)
            elif group == "real":
                reals.append(item)
        if demos and reals:
            self.client.account_mode = "AMBIGUOUS"
            self.client.account_balance = None
            self.client.account_currency = ""
        elif demos:
            self.client.account_mode = "DEMO"
            self.client.account_balance, self.client.account_currency = max(demos)
        elif reals:
            self.client.account_mode = "REAL"
            self.client.account_balance, self.client.account_currency = max(reals)

    async def _assets(self, msg):
        found = set()

        def visit(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if str(k).lower() in {"pair", "symbol", "asset", "instrument"}:
                        values = v if isinstance(v, list) else [v]
                        for z in values:
                            if isinstance(z, dict):
                                s = str(z.get("id", z.get("pair", z.get("symbol", ""))))
                            else:
                                s = str(z)
                            s = s.upper().strip()
                            if 3 <= len(s) <= 30 and s.replace("_", "").isalnum():
                                found.add(s)
                    else:
                        visit(v)
            elif isinstance(x, list):
                for z in x:
                    visit(z)

        visit(msg.get("d") if isinstance(msg, dict) else None)
        new = found - self.assets
        for asset in new:
            await self._subscribe_asset(asset)
        if found:
            self.assets.update(found)
            log.info("ASSET_DISCOVERY found=%s total=%s", len(found), len(self.assets))

    def _put_candle(self, asset, candle, notify=False):
        ts = float(candle["timestamp"])
        bucket = int(ts // 60) * 60
        candle = dict(candle)
        candle["timestamp"] = float(bucket)
        old = {x["timestamp"]: x for x in self.history[asset]}
        old[bucket] = candle
        ordered = sorted(old.values(), key=lambda z: z["timestamp"])[-360:]
        self.history[asset] = deque(ordered, maxlen=360)
        if notify and bucket > self.last_completed[asset]:
            self.last_completed[asset] = bucket
            self.candles += 1
            self.on_candle(asset, dict(candle))

    def _consume_history(self, asset, msg):
        rows = []
        for x in walk_candles(msg):
            p = normalize(x, asset)
            if p and p[0] == asset:
                rows.append(p[1])
        for candle in rows:
            self._put_candle(asset, candle, notify=False)

    async def _candle_response(self, msg):
        for x in walk_candles(msg):
            p = normalize(x)
            if not p:
                continue
            asset, candle = p
            self.assets.add(asset)
            self._put_candle(asset, candle, notify=True)

    async def _tick(self, msg):
        rows = msg.get("d", []) if isinstance(msg, dict) else []
        if isinstance(rows, dict):
            rows = [rows]
        for x in rows if isinstance(rows, list) else []:
            if not isinstance(x, dict):
                continue
            asset = str(x.get("p", x.get("pair", x.get("symbol", "")))).upper().strip()
            price = num(x.get("q", x.get("price", x.get("close"))))
            ts = num(x.get("t", x.get("timestamp", x.get("time"))))
            if not asset or price is None or ts is None:
                continue
            if ts > 1e10:
                ts /= 1000
            self.assets.add(asset)
            self.ticks += 1
            bucket = int(ts // 60) * 60
            cur = self.forming.get(asset)
            if cur is None or cur["timestamp"] != bucket:
                if cur and cur["timestamp"] > self.last_completed[asset]:
                    self._put_candle(asset, cur, notify=True)
                cur = {"open": price, "high": price, "low": price, "close": price, "timestamp": float(bucket)}
                self.forming[asset] = cur
            else:
                cur["high"] = max(cur["high"], price)
                cur["low"] = min(cur["low"], price)
                cur["close"] = price

    async def _poll_loop(self):
        while self.connected:
            for asset in list(self.subscribed):
                try:
                    self._consume_history(asset, await self.client.request_candles(asset, 360))
                except Exception as e:
                    log.debug("HISTORY_REFRESH_FAILED asset=%s error=%s", asset, type(e).__name__)
            await asyncio.sleep(60)

    def snapshot(self, asset):
        return list(self.history.get(asset, ()))

    def live_price(self, asset):
        cur = self.forming.get(asset)
        if cur:
            return float(cur["close"])
        history = self.history.get(asset)
        return float(history[-1]["close"]) if history else None

    def status(self):
        return {
            "connected": self.connected,
            "assets": sorted(self.assets),
            "subscribed": len(self.subscribed),
            "ticks": self.ticks,
            "completed_1m": self.candles,
            "history_1m": {a: len(self.history[a]) for a in sorted(self.assets)},
            "account_mode": self.client.account_mode,
            "account_balance": self.client.account_balance,
            "account_currency": self.client.account_currency,
        }

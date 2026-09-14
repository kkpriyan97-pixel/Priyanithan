from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

log = logging.getLogger("candice.olymp_live")
UPSTREAM = "https://github.com/ChipaDevTeam/OlympTradeAPI.git"
LOCAL_API = Path("/tmp/candice_olymptrade_api")


def _clear_telegram_webhook():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/deleteWebhook"
        data = urllib.parse.urlencode({"drop_pending_updates": "false"}).encode()
        with urllib.request.urlopen(urllib.request.Request(url, data=data, method="POST"), timeout=8) as r:
            ok = bool(json.loads(r.read().decode()).get("ok"))
        log.info("Telegram polling startup: stale webhook cleared=%s", ok)
    except Exception as e:
        log.warning("Telegram webhook cleanup failed (token not logged): %s", type(e).__name__)


_clear_telegram_webhook()


def _load():
    target = LOCAL_API / "olymptrade_ws"
    if not target.exists():
        subprocess.run(["git", "clone", "--depth", "1", UPSTREAM, str(LOCAL_API)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=60)
    sys.path.insert(0, str(LOCAL_API))
    from olymptrade_ws.core.client import OlympTradeClient
    from olymptrade_ws.olympconfig import parameters
    return OlympTradeClient, parameters


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rows(value):
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_rows(item))
        return out
    if not isinstance(value, dict):
        return []
    candles = value.get("candles")
    if isinstance(candles, list):
        parent_asset = value.get("p", value.get("pair", value.get("symbol")))
        out = []
        for item in candles:
            if isinstance(item, dict) and parent_asset and not any(k in item for k in ("p", "pair", "symbol")):
                item = dict(item); item["p"] = parent_asset
            out.extend(_rows(item))
        return out
    for key in ("data", "d", "items", "result"):
        val = value.get(key)
        if isinstance(val, (list, dict)):
            nested = _rows(val)
            if nested:
                return nested
    return [value]


_ASSET_RE = re.compile(r"^[A-Z0-9]{3,30}(?:_[A-Z0-9]+)*$")
_ASSET_KEYS = {"p", "pair", "symbol", "asset", "instrument", "instrument_id"}
_COLLECTION_KEYS = {"assets", "pairs", "instruments", "symbols", "markets"}


def _extract_assets(value):
    """Extract plausible instrument symbols only from explicit market fields."""
    found = set()
    def walk(v, key=""):
        if isinstance(v, dict):
            for k, item in v.items():
                lk = str(k).lower()
                if lk in _ASSET_KEYS:
                    vals = item if isinstance(item, list) else [item]
                    for candidate in vals:
                        if isinstance(candidate, dict):
                            for kk in ("p", "pair", "symbol", "asset"):
                                s = str(candidate.get(kk, "")).upper().strip()
                                if _ASSET_RE.fullmatch(s): found.add(s)
                        else:
                            s = str(candidate).upper().strip()
                            if _ASSET_RE.fullmatch(s): found.add(s)
                elif lk in _COLLECTION_KEYS and isinstance(item, (list, dict)):
                    walk(item, lk)
                else:
                    walk(item, lk)
        elif isinstance(v, list):
            for item in v: walk(item, key)
    walk(value)
    return found


class OlympLiveFeed:
    """Read-only Olymp market feed; builds 1m candles from live ticks."""

    def __init__(self, on_candle: Callable[[str, dict], None], on_history: Callable[[str, dict], None] | None = None):
        self.on_candle = on_candle
        self.on_history = on_history
        self.token = os.getenv("OLYMPTRADE_ACCESS_TOKEN", "").strip()
        configured = [x.strip().upper() for x in os.getenv("OLYMPTRADE_ASSETS", "ASIA_X").split(",") if x.strip()]
        self.assets = list(dict.fromkeys(configured))
        self.configured_assets = list(self.assets)
        self.discovered_assets = set()
        self.enabled = bool(self.token)
        self.connected = False
        self.thread = None
        self.client = None
        self.forming = {}
        self.tick_count = 0
        self.history_attempted = set()
        self._subscribe_lock = None

    def start(self):
        if not self.enabled:
            log.warning("Olymp live feed disabled: token not configured")
            return
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            asyncio.run(self._main())
        except Exception:
            log.exception("Olymp live feed stopped")
            self.connected = False

    async def _fetch_history(self, asset: str):
        """Use the upstream market helper and treat unavailable history as a soft failure.
        Some session-visible instruments stream ticks but reject historical event-10 requests.
        Live analysis must continue rather than retrying and flooding the log.
        """
        if asset in self.history_attempted:
            return []
        self.history_attempted.add(asset)
        try:
            response = await self.client.market.get_candles(pair=asset, size=60, count=80, end_time=int(time.time()))
            return response or []
        except Exception as e:
            log.warning("Historical candles unavailable for %s; live-tick warmup will be used (%s)", asset, type(e).__name__)
            return []

    async def _subscribe_asset(self, asset: str):
        try:
            await self.client.market.subscribe_ticks(asset)
            log.info("Subscribed to Olymp live ticks: %s", asset)
            return True
        except Exception:
            log.exception("Failed to subscribe to Olymp live ticks: %s", asset)
            return False

    async def _metadata_cb(self, message):
        try:
            found = _extract_assets(message)
            new = found - set(self.assets)
            if not new:
                return
            self.discovered_assets.update(new)
            self.assets.extend(sorted(new))
            log.info("OLYMP_ASSETS_DISCOVERED count=%s assets=%s", len(self.assets), sorted(new))
            for asset in sorted(new):
                # Subscribe first so an asset can start warming up immediately.
                await self._subscribe_asset(asset)
                try:
                    history = await self._fetch_history(asset)
                    seeded = await self._history(history, asset)
                    if seeded:
                        log.info("HISTORY_SEEDED asset=%s count=%s", asset, seeded)
                    else:
                        log.info("LIVE_WARMUP asset=%s; historical seed unavailable", asset)
                except Exception:
                    log.exception("Failed history seed for discovered asset=%s", asset)
        except Exception:
            log.exception("Olymp asset metadata parsing failed")

    async def _main(self):
        try:
            Client, parameters = _load()
        except Exception:
            log.exception("Unable to load OlympTrade API source")
            return
        self.client = Client(access_token=self.token)

        async def tick_cb(message):
            await self._ticks(message)
        self.client.register_callback(parameters.E_TICK_UPDATE, tick_cb)

        # The upstream client mirrors the browser startup sequence. Register
        # metadata callbacks before it runs so account/session market instruments
        # can be discovered and subscribed read-only.
        for event_code in (220, 110, 700, 112, 140, 1038, 1037, 1039, 141, 22, 26, 111, 1054, 1076, 1301, 1097, 241, 230, 231, 75, 1055, 2223, 2301, 55, 150, 152, 151, 126, 602, 601, 2076):
            self.client.register_callback(event_code, self._metadata_cb)

        try:
            await self.client.start()
            self.connected = True
            try:
                await self.client.initialize_session()
                log.info("Olymp browser-like session initialization completed; account market metadata requested")
            except Exception:
                log.exception("Olymp session initialization failed; continuing with configured assets")

            # Keep explicitly configured assets as a safe fallback. Session metadata
            # can add more instruments dynamically without enabling any order API.
            initial = list(dict.fromkeys(self.assets))
            for asset in initial:
                try:
                    history = await self._fetch_history(asset)
                    seeded = await self._history(history, asset)
                    if seeded:
                        log.info("HISTORY_SEEDED asset=%s count=%s", asset, seeded)
                    else:
                        log.info("LIVE_WARMUP asset=%s; historical seed unavailable", asset)
                except Exception:
                    log.exception("Failed to load Olymp candle history: %s", asset)
                await self._subscribe_asset(asset)

            while self.client.connection.is_connected:
                await asyncio.sleep(15)
        except Exception:
            log.exception("Olymp live market connection error")
        finally:
            self.connected = False
            try:
                await self.client.stop()
            except Exception:
                pass

    async def _history(self, history, default_asset=""):
        if not self.on_history: return 0
        parsed = []
        for x in _rows(history):
            asset = str(x.get("p", x.get("pair", x.get("symbol", default_asset))) or default_asset).upper()
            o, hi, lo, c = _num(x.get("open", x.get("o"))), _num(x.get("high", x.get("h"))), _num(x.get("low", x.get("l"))), _num(x.get("close", x.get("c")))
            t = _num(x.get("t", x.get("timestamp", x.get("time"))))
            if None in (o, hi, lo, c, t) or not asset: continue
            if t > 10_000_000_000: t /= 1000.0
            if hi < max(o, c) or lo > min(o, c) or hi < lo: continue
            parsed.append((asset, t, {"open": o, "high": hi, "low": lo, "close": c, "timestamp": t}))
        parsed.sort(key=lambda z: z[1])
        seen, count = set(), 0
        for asset, t, candle in parsed:
            key = (asset, int(t))
            if key in seen: continue
            seen.add(key); self.on_history(asset, candle); count += 1
        return count

    async def _ticks(self, message):
        rows = message.get("d", []) if isinstance(message, dict) else message
        if isinstance(rows, dict): rows = [rows]
        if not isinstance(rows, list): return
        for x in rows:
            if not isinstance(x, dict): continue
            asset = str(x.get("p", x.get("pair", x.get("symbol", "")))).upper()
            try:
                price = float(x.get("q", x.get("price", x.get("close"))))
                ts = float(x.get("t", x.get("timestamp", x.get("time"))))
            except (TypeError, ValueError): continue
            if asset not in self.assets: continue
            if ts > 10_000_000_000: ts /= 1000.0
            self.tick_count += 1
            if self.tick_count == 1: log.info("First live Olymp tick received: asset=%s price=%s", asset, price)
            bucket = int(ts // 60) * 60
            cur = self.forming.get(asset)
            if cur is None or cur["timestamp"] != bucket:
                if cur is not None: self.on_candle(asset, cur)
                cur = {"open": price, "high": price, "low": price, "close": price, "timestamp": float(bucket)}
                self.forming[asset] = cur
            else:
                cur["high"] = max(cur["high"], price); cur["low"] = min(cur["low"], price); cur["close"] = price

    def status(self):
        return {"configured": self.enabled, "connected": self.connected, "assets": self.assets,
                "configured_assets": self.configured_assets, "discovered_assets": sorted(self.discovered_assets),
                "asset_discovery": "session_metadata", "mode": "READ_ONLY_MARKET_DATA",
                "timeframe": "1m_from_live_ticks", "auto_trade": False, "ticks_received": self.tick_count}

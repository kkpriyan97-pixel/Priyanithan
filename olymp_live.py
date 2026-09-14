from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("candice.olymp_live")
UPSTREAM = "https://github.com/ChipaDevTeam/OlympTradeAPI.git"
LOCAL_API = Path("/tmp/candice_olymptrade_api")


def _load_olymp_client():
    """Load the upstream client without installing it as a pip package.

    The upstream repository currently has no setup.py/pyproject.toml, so Render
    cannot install it from requirements.txt. We clone the pinned source at
    runtime instead. No trading methods are called by Candice.
    """
    target = LOCAL_API / "olymptrade_ws"
    if not target.exists():
        LOCAL_API.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", UPSTREAM, str(LOCAL_API)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
    sys.path.insert(0, str(LOCAL_API))
    from olymptrade_ws.core.client import OlympTradeClient
    from olymptrade_ws.olympconfig import parameters
    return OlympTradeClient, parameters


class OlympLiveFeed:
    """Read-only Olymp Trade market-data bridge."""

    def __init__(self, on_candle: Callable[[str, dict], None]):
        self.on_candle = on_candle
        self.token = os.getenv("OLYMPTRADE_ACCESS_TOKEN", "").strip()
        self.assets = [x.strip().upper() for x in os.getenv("OLYMPTRADE_ASSETS", "ASIA_X").split(",") if x.strip()]
        self.enabled = bool(self.token)
        self.connected = False
        self.thread: threading.Thread | None = None
        self.client: Any = None

    def start(self) -> None:
        if not self.enabled:
            log.warning("Olymp live feed disabled: OLYMPTRADE_ACCESS_TOKEN is not configured")
            return
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name="olymp-live-feed", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception:
            log.exception("Olymp live feed stopped")
            self.connected = False

    async def _main(self) -> None:
        try:
            OlympTradeClient, parameters = _load_olymp_client()
        except Exception:
            log.exception("Unable to load OlympTrade API source")
            return

        self.client = OlympTradeClient(access_token=self.token)

        async def candle_callback(message: Any) -> None:
            await self._handle_candle_message(message)

        self.client.register_callback(parameters.E_CANDLE_TIME_UPDATE, candle_callback)

        try:
            await self.client.start()
            self.connected = True
            log.info("Olymp live market connection established; assets=%s", self.assets)

            for asset in self.assets:
                try:
                    history = await self.client.market.get_candles(asset, size=60, count=100)
                    await self._handle_candle_message(history if isinstance(history, dict) else {"d": history})
                    log.info("Loaded Olymp 1m candle history: %s", asset)
                except Exception:
                    log.exception("Failed to load Olymp candle history: %s", asset)
                try:
                    await self.client.market.subscribe_candles(asset, size=60)
                    log.info("Subscribed to Olymp 1m candles: %s", asset)
                except Exception:
                    log.exception("Failed to subscribe to Olymp candles: %s", asset)

            while self.client.connection.is_connected:
                await asyncio.sleep(10)
        except Exception:
            log.exception("Olymp live market connection error")
        finally:
            self.connected = False
            try:
                await self.client.stop()
            except Exception:
                pass

    async def _handle_candle_message(self, message: Any) -> None:
        items = message.get("d", []) if isinstance(message, dict) else message
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            asset = str(item.get("p", item.get("pair", item.get("symbol", "")))).upper()
            if not asset:
                continue
            o = item.get("open", item.get("o"))
            h = item.get("high", item.get("h"))
            l = item.get("low", item.get("l"))
            c = item.get("close", item.get("c"))
            t = item.get("time", item.get("timestamp", item.get("t")))
            try:
                candle = {"open": float(o), "high": float(h), "low": float(l), "close": float(c),
                          "timestamp": float(t) if t is not None else None}
            except (TypeError, ValueError):
                continue
            if candle["high"] < max(candle["open"], candle["close"]):
                continue
            if candle["low"] > min(candle["open"], candle["close"]):
                continue
            self.on_candle(asset, candle)

    def status(self) -> dict:
        return {"configured": self.enabled, "connected": self.connected,
                "assets": self.assets, "mode": "READ_ONLY_MARKET_DATA", "auto_trade": False}

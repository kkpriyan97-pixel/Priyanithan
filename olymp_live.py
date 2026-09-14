from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any, Callable

log = logging.getLogger("candice.olymp_live")


class OlympLiveFeed:
    """Read-only Olymp Trade market-data bridge.

    This module never calls any order/trade method. It only reads historical
    candles and subscribes to live 1-minute candles, then forwards them to the
    Candice analysis engine.
    """

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
            from olymptrade_ws.main import OlympTradeClient
            from olymptrade_ws.olympconfig import parameters
        except Exception:
            log.exception("OlympTrade API library is unavailable")
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
                # Seed Candice with recent 1m history so it can analyse immediately.
                try:
                    history = await self.client.market.get_candles(asset, size=60, count=100)
                    await self._handle_candle_message(history if isinstance(history, dict) else {"d": history})
                    log.info("Loaded Olymp candle history: %s", asset)
                except Exception:
                    log.exception("Failed to load Olymp candle history: %s", asset)
                try:
                    await self.client.market.subscribe_candles(asset, size=60)
                    log.info("Subscribed to Olymp 1m candles: %s", asset)
                except Exception:
                    log.exception("Failed to subscribe to Olymp candles: %s", asset)

            while True:
                await asyncio.sleep(30)
                if hasattr(self.client, "is_connected") and not self.client.is_connected:
                    self.connected = False
                    break
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
                candle = {
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "timestamp": float(t) if t is not None else None,
                }
            except (TypeError, ValueError):
                continue
            if candle["high"] < max(candle["open"], candle["close"]):
                continue
            if candle["low"] > min(candle["open"], candle["close"]):
                continue
            self.on_candle(asset, candle)

    def status(self) -> dict:
        return {
            "configured": self.enabled,
            "connected": self.connected,
            "assets": self.assets,
            "mode": "READ_ONLY_MARKET_DATA",
            "auto_trade": False,
        }

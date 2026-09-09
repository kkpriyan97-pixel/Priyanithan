"""Compatibility helpers for the signal bot runtime."""

from .core.client import OlympTradeClient


async def _get_candles(self, pair, size, count, end_time=None):
    """Expose MarketAPI.get_candles on the client for legacy callers."""
    return await self.market.get_candles(
        pair=pair,
        size=size,
        count=count,
        end_time=end_time,
    )


# app.py historically calls ot_client.get_candles(...), while the current
# client exposes the implementation as ot_client.market.get_candles(...).
# Keep that legacy call working without changing trading behavior.
if not hasattr(OlympTradeClient, "get_candles"):
    OlympTradeClient.get_candles = _get_candles

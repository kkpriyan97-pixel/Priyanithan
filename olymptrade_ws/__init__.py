"""OlympTrade websocket package bootstrap.

The trading application owns the signal/Telegram workflow. This package only
exposes the broker client and market API; no legacy hotfix modules are imported
implicitly at package import time.
"""

from .core.client import OlympTradeClient

try:
    from .api.market import MarketAPI
except Exception:
    MarketAPI = None

__all__ = ["OlympTradeClient", "MarketAPI"]

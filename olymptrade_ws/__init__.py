"""OlympTrade websocket package bootstrap.

The trading application owns the signal/Telegram workflow. This package only
exposes the broker client and market API. The fresh AI adapter is loaded here
and waits until app.py has defined its AI function; legacy hotfix modules are
not imported.
"""

from .core.client import OlympTradeClient

try:
    from .api.market import MarketAPI
except Exception:
    MarketAPI = None

try:
    import fresh_ai_runtime
except Exception:
    fresh_ai_runtime = None

__all__ = ["OlympTradeClient", "MarketAPI"]

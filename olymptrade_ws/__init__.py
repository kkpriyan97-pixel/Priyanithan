"""Read-only OlympTrade market-data connector used by Candice v8."""
from .core.client import OlympTradeClient
try:
    from .api.market import MarketAPI
except Exception:
    MarketAPI = None
__all__ = ['OlympTradeClient', 'MarketAPI']

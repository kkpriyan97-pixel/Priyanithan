# olymptrade_ws/__init__.py
# Expose main classes for easy import
from .main import OlympTradeClient
from .core.client import OlympTradeClient as CoreOlympTradeClient
from .api.balance import BalanceAPI
from .api.market import MarketAPI
from .api.trade import TradeAPI
from . import compat as _compat

__all__ = [
    "OlympTradeClient",
    "CoreOlympTradeClient",
    "BalanceAPI",
    "MarketAPI",
    "TradeAPI"
]

# Start final runtime layers after app.py becomes available.
try:
    import signal_engine  # noqa: F401
except Exception:
    pass
try:
    import runtime_fixes  # noqa: F401
except Exception:
    pass
try:
    import scan_bootstrap_fix  # noqa: F401
except Exception:
    pass

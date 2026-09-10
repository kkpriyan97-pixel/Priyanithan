"""Olymptrade websocket package bootstrap."""

# Public client used by app.py.
from .core.client import OlympTradeClient

try:
    from .api.market import MarketAPI
except Exception:
    pass

# app.py calls ot_client.get_candles(...), while the current client exposes
# the implementation as ot_client.market.get_candles(...). Import the
# compatibility shim BEFORE signal_engine starts its runtime installer.
try:
    from . import compat as _compat
except Exception:
    pass

# Clean up stale/invalid AI provider environment variables before the
# manual-entry AI patch builds its provider chain.
try:
    import provider_cleanup_hotfix
except Exception:
    pass

try:
    import signal_engine
except Exception:
    pass
try:
    import runtime_fixes
except Exception:
    pass
try:
    import scan_bootstrap_fix
except Exception:
    pass
try:
    import analysis_hotfix
except Exception:
    pass

# NOTE: demo_lock_hotfix.py is intentionally retained in the repository for
# rollback/reference, but is no longer imported. The active flow supports
# explicit DEMO or REAL selection; AUTO_TRADE remains OFF.

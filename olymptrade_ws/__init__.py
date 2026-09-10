"""Olymptrade websocket package bootstrap."""

# Public client used by app.py.
from .core.client import OlympTradeClient

try:
    from .api.market import MarketAPI
except Exception:
    pass

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

# Normalize successful Groq responses when the model returned explicit
# decision fields without valid JSON. This never invents a decision.
try:
    import ai_response_hotfix
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

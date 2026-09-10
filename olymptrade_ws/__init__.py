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

try:
    import provider_cleanup_hotfix
except Exception:
    pass

try:
    import ai_response_hotfix
except Exception:
    pass

try:
    import indicator_method_hotfix
except Exception:
    pass

try:
    import ai_method_prompt_hotfix
except Exception:
    pass

# Limit the number of AI confirmations in one scan to protect small-provider
# token-per-minute budgets. No signal is forced when AI is unavailable.
try:
    import ai_rate_limit_hotfix
except Exception:
    pass

try:
    import signal_engine
except Exception:
    pass

try:
    import scan_cadence_hotfix
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

# AUTO_TRADE remains OFF.

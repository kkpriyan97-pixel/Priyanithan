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

# Normalize successful Groq responses using strict Structured Outputs when
# available. This never invents a decision.
try:
    import ai_response_hotfix
except Exception:
    pass

# Final reference-style technical method: PSAR, MA, EMA, Donchian, MACD, ROC
# with a live candle trigger and a complete numeric snapshot for AI.
try:
    import indicator_method_hotfix
except Exception:
    pass

# Final AI confirmation prompt: the model receives the exact numeric indicator
# snapshot and must confirm/reject it rather than infer missing values.
try:
    import ai_method_prompt_hotfix
except Exception:
    pass

# Final scanner: broad broker/profitability universe + 1m trigger + 5m context.
try:
    import signal_engine
except Exception:
    pass

# Scan every minute so a 1-minute/short-expiry opportunity is not missed
# between the old five-minute scan boundaries.
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

# demo_lock_hotfix.py is retained for rollback/reference only.
# AUTO_TRADE remains OFF.

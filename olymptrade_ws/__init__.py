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

try:
    import trade_asset_selection_hotfix
except Exception:
    pass

try:
    import final_loss_reanalysis_hotfix
except Exception:
    pass

try:
    import disable_trade_now_hotfix
except Exception:
    pass

try:
    import final_five_minute_cadence_hotfix
except Exception:
    pass

try:
    import final_asset_selection_flow
except Exception:
    pass

try:
    import final_asset_flow_enforcer
except Exception:
    pass

# AUTO_TRADE remains OFF.

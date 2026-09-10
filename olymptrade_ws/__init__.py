"""Olymptrade websocket package bootstrap."""
try:
    from .api.market import MarketAPI
except Exception:
    try:
        from .market import MarketAPI
    except Exception:
        MarketAPI = None

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

"""Minimal runtime startup hooks for Priyanithan.

The Telegram command flow is implemented directly in app.py. Legacy Telegram
flow patchers are intentionally not imported here, preventing them from
rewriting /start or /access at runtime.

No broker order execution or automatic trading is enabled here.
"""

try:
    import signal_gate_compat_hotfix  # noqa: F401
except Exception:
    pass

try:
    import live_ai_monitor_hotfix  # noqa: F401
except Exception:
    pass

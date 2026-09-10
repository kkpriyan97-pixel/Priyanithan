"""Runtime startup hooks for the Priyanithan signal engine."""

# Read-only signal/monitor hardening. No module here enables broker order
# execution or automatic trading.
try:
    import signal_gate_compat_hotfix  # noqa: F401
except Exception:
    pass
try:
    import live_ai_monitor_hotfix  # noqa: F401
except Exception:
    pass
try:
    import one_minute_candle_mode_hotfix  # noqa: F401
except Exception:
    pass
try:
    import final_access_asset_lock_v2  # noqa: F401
except Exception:
    pass

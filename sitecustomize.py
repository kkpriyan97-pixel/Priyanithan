"""Runtime startup hooks for the Priyanithan signal engine."""

# These modules are read-only signal/monitor hardening. They never enable
# broker order execution or automatic trading.
try:
    import signal_gate_compat_hotfix  # noqa: F401
except Exception:
    pass
try:
    import live_ai_monitor_hotfix  # noqa: F401
except Exception:
    pass

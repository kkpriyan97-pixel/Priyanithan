"""Runtime startup hooks for the Priyanithan signal engine."""

# Explicit startup import for compatibility hardening. This module never
# enables broker login, broker order execution, or automatic trading.
try:
    import signal_gate_compat_hotfix  # noqa: F401
except Exception:
    pass

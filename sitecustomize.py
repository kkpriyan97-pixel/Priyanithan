"""Runtime startup hooks for the Priyanithan signal engine.

Loads only the compatibility hardening needed by the signal engine. This
module never enables broker order execution or automatic trading.
"""

try:
    import signal_gate_compat_hotfix  # noqa: F401
except Exception:
    pass

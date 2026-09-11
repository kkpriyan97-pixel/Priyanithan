"""Minimal runtime startup hooks for Priyanithan.

The Telegram command flow is implemented directly in app.py. Legacy Telegram
flow patchers are intentionally not imported here, preventing them from
rewriting /start or /access at runtime.

No broker order execution or automatic trading is enabled here.
"""

try:
    import build_application_hotfix  # noqa: F401
except Exception:
    pass
try:
    import signal_gate_compat_hotfix  # noqa: F401
except Exception:
    pass
try:
    import live_asset_probe_hotfix  # noqa: F401
except Exception:
    pass
try:
    import live_asset_refresh_hotfix  # noqa: F401
except Exception:
    pass
try:
    import live_asset_probe_v2_hotfix  # noqa: F401
except Exception:
    pass
try:
    import live_ai_monitor_hotfix  # noqa: F401
except Exception:
    pass
try:
    import final_result_precision_hotfix  # noqa: F401
except Exception:
    pass
try:
    import digital_countdown_hotfix  # noqa: F401
except Exception:
    pass
try:
    import loss_recovery_ai_hotfix  # noqa: F401
except Exception:
    pass
try:
    import selected_scan_cycle_hotfix  # noqa: F401
except Exception:
    pass
try:
    import candice_memory_integration_hotfix  # noqa: F401
except Exception:
    pass
try:
    import duration_selection_hotfix  # noqa: F401
except Exception:
    pass
try:
    import signal_window_countdown_hotfix  # noqa: F401
except Exception:
    pass
try:
    import signal_live_update_hotfix  # noqa: F401
except Exception:
    pass
try:
    import native_live_signal_monitor_hotfix  # noqa: F401
except Exception:
    pass
try:
    import candice_research_brain_hotfix  # noqa: F401
except Exception:
    pass
try:
    import candice_ai_memory_context_hotfix  # noqa: F401
except Exception:
    pass
try:
    import candice_timeframe_strategy_brain_hotfix  # noqa: F401
except Exception:
    pass
try:
    import candice_compare_learn_improve_hotfix  # noqa: F401
except Exception:
    pass
try:
    import candice_signal_session_hotfix  # noqa: F401
except Exception:
    pass

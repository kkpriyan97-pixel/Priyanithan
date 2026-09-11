"""Disable the legacy fast_manual_entry Telegram command patchers.

The legacy module still contains an old /access handler that exposes the
DEMO/REAL chooser. Keep its AI/route/sender helpers available, but prevent its
background bootstrap from rewriting /start and /access after the final flow
has installed them. No broker order execution is enabled here.
"""

try:
    import fast_manual_entry as _legacy

    def _disabled(*args, **kwargs):
        return False

    _legacy.patch_start = _disabled
    _legacy.patch_access = _disabled
except Exception:
    pass

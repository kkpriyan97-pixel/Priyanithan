"""Live-scan bootstrap guard.

If broker instrument discovery is delayed at startup, temporarily use only
explicitly configured/manual fallback symbols so the first scan does not
silently run over an empty universe. Once broker discovery succeeds, AUTO
DISCOVERY is restored. This module never places trades.
"""
import os
import sys
import threading
import time


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _fallback_pairs(a):
    configured = [x.strip().upper() for x in os.getenv("OLYMP_PAIRS", "").split(",") if x.strip() and x.strip().upper() != "AUTO"]
    if configured:
        return configured
    aliases = getattr(a, "PAIR_ALIASES", {}) or {}
    values = [str(v).strip().upper() for v in aliases.values() if str(v).strip()]
    # ASIA_X is the existing broker symbol used by this project for the
    # Asia Composite Index signal family.
    if "ASIA_X" not in values:
        values.append("ASIA_X")
    return list(dict.fromkeys(values))


def _install():
    a = _app()
    if a is None or getattr(a, "_SCAN_BOOTSTRAP_FIX", False):
        return bool(a and getattr(a, "_SCAN_BOOTSTRAP_FIX", False))

    def guard_loop():
        # Wait until app.py has completed its globals and Telegram runtime is
        # available. Then keep the fallback only while broker discovery is empty.
        for _ in range(180):
            a2 = _app()
            if a2 is None:
                time.sleep(1)
                continue
            try:
                discovered = getattr(a2, "discovered_assets", {})
                if discovered:
                    a2.AUTO_DISCOVER_ASSETS = True
                    a2.PAIRS[:] = sorted(discovered.keys())[:getattr(a2, "MAX_ASSETS_PER_CYCLE", 120)]
                    a2.log.info("SCAN BOOTSTRAP: live broker assets ready (%s); auto discovery enabled", len(discovered))
                else:
                    fallback = _fallback_pairs(a2)
                    if fallback:
                        a2.PAIRS[:] = fallback[:getattr(a2, "MAX_ASSETS_PER_CYCLE", 120)]
                        a2.AUTO_DISCOVER_ASSETS = False
                        a2.log.warning("SCAN BOOTSTRAP: broker asset discovery delayed; using configured fallback symbols=%s", a2.PAIRS)
                return
            except Exception as exc:
                try:
                    a2.log.warning("SCAN BOOTSTRAP: initialization retry failed: %s", exc)
                except Exception:
                    pass
            time.sleep(1)

    a._SCAN_BOOTSTRAP_FIX = True
    threading.Thread(target=guard_loop, name="scan-bootstrap-guard", daemon=True).start()
    return True


try:
    _install()
except Exception:
    pass

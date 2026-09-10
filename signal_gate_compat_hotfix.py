"""Compatibility hardening for the final signal engine.

Normalizes harmless AI provider wording differences and keeps expiry choices
consistent with the bot's supported fixed-time choices. This patch never
creates or enables broker order execution.
"""
import threading
import time


def _normalize_decision(value):
    v = str(value or "").strip().upper().replace("_", " ")
    if v in {"APPROVE", "APPROVED", "ACCEPT", "ACCEPTED"}:
        return "APPROVE"
    if v in {"REJECT", "REJECTED", "DECLINE", "DECLINED"}:
        return "REJECT"
    return v


def _normalize_direction(value):
    v = str(value or "").strip().upper().replace("_", " ")
    if v in {"BUY", "CALL", "LONG", "UP"}:
        return "UP"
    if v in {"SELL", "PUT", "SHORT", "DOWN"}:
        return "DOWN"
    if v in {"NO SIGNAL", "NONE", "NEUTRAL"}:
        return "NO SIGNAL"
    return v


def _patch():
    try:
        import app
        import signal_engine
    except Exception:
        return False

    signal_engine.EXPIRIES = (1, 2, 4, 5, 15)

    if not getattr(app, "_SIGNAL_GATE_COMPAT_V1", False):
        original = getattr(app, "call_ai", None)
        if callable(original):
            def safe_call_ai(prompt):
                result, err = original(prompt)
                if isinstance(result, dict) and not err:
                    result = dict(result)
                    result["decision"] = _normalize_decision(result.get("decision"))
                    result["direction"] = _normalize_direction(result.get("direction"))
                    try:
                        result["confidence"] = max(0, min(100, int(result.get("confidence", 0))))
                    except Exception:
                        return None, "Invalid AI confidence"
                    try:
                        result["duration_min"] = int(result.get("duration_min", 5))
                    except Exception:
                        return None, "Invalid AI duration"
                return result, err
            app.call_ai = safe_call_ai
            app._SIGNAL_GATE_COMPAT_V1 = True
            try:
                app.log.warning("SIGNAL GATE COMPAT V1 ACTIVE: AI wording normalization + expiry set 1/2/4/5/15")
            except Exception:
                pass
    return True


def _boot():
    for _ in range(1800):
        try:
            if _patch():
                return
        except Exception:
            pass
        time.sleep(0.1)


threading.Thread(target=_boot, name="signal-gate-compat", daemon=True).start()

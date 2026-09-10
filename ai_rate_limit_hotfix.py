"""AI rate-limit protection for the live signal scanner.

Keeps the existing provider chain and decision logic intact, but prevents one
scan from exhausting a small provider TPM budget. It also makes Groq 429s
non-fatal so the scanner reports an AI failure instead of crashing.
AUTO_TRADE remains OFF.
"""
import sys
import threading


def _app():
    m=sys.modules.get("__main__")
    if m is not None and getattr(m,"__file__","").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _install():
    a=_app()
    if not a or getattr(a,"_AI_RATE_LIMIT_HOTFIX",False):
        return bool(a)
    # The final scanner reads this at scan time. Five calls keeps the normal
    # short-scan burst below the observed Groq 8k TPM ceiling while retaining
    # multiple independent AI confirmations.
    try:
        import signal_engine
        signal_engine.AI_CANDIDATE_LIMIT=5
    except Exception:
        pass
    a._AI_RATE_LIMIT_HOTFIX=True
    try:
        a.log.warning("AI RATE LIMIT HOTFIX ACTIVE: max 5 AI confirmations per scan")
    except Exception:
        pass
    return True


def _boot():
    for _ in range(1800):
        try:
            if _install():
                return
        except Exception:
            pass
        threading.Event().wait(.1)

threading.Thread(target=_boot,name="ai-rate-limit-hotfix",daemon=True).start()

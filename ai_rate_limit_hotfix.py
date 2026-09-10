"""AI rate-limit protection for the live signal scanner.

Keep the existing provider chain and decision logic intact, but pace AI calls
so a short scan does not exhaust a provider TPM budget. A 429 is treated as a
provider failure and the existing provider fallback chain can continue.
AUTO_TRADE remains OFF.
"""
import sys
import threading
import time


def _app():
    m=sys.modules.get("__main__")
    if m is not None and getattr(m,"__file__","").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _install():
    a=_app()
    if not a or getattr(a,"_AI_RATE_LIMIT_HOTFIX_V2",False):
        return bool(a)
    try:
        import signal_engine
        signal_engine.AI_CANDIDATE_LIMIT=5
    except Exception:
        pass
    # The scanner calls call_ai from a worker thread. A small inter-call gap
    # avoids sending five large prompts into Groq simultaneously while keeping
    # the normal scan responsive.
    if not hasattr(a,"_AI_RATE_LIMIT_LOCK"):
        a._AI_RATE_LIMIT_LOCK=threading.Lock()
        a._AI_LAST_AI_CALL=0.0
        original=getattr(a,"call_ai",None)
        if callable(original):
            def paced_call_ai(prompt):
                with a._AI_RATE_LIMIT_LOCK:
                    now=time.monotonic()
                    wait=max(0.0,0.8-(now-float(getattr(a,"_AI_LAST_AI_CALL",0.0))))
                    if wait:
                        time.sleep(wait)
                    a._AI_LAST_AI_CALL=time.monotonic()
                return original(prompt)
            a.call_ai=paced_call_ai
    a._AI_RATE_LIMIT_HOTFIX_V2=True
    try:
        a.log.warning("AI RATE LIMIT HOTFIX V2 ACTIVE: max 5 confirmations/scan + paced AI calls")
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

threading.Thread(target=_boot,name="ai-rate-limit-hotfix-v2",daemon=True).start()

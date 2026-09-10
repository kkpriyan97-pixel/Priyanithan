"""Tell the existing AI confirmer to evaluate the same reference indicator set."""
import sys
import threading
import time


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _install():
    a = _app()
    if not a or getattr(a, "_REFERENCE_AI_PROMPT", False):
        return bool(a)
    original = getattr(a, "ai_prompt", None)
    if not callable(original):
        return False

    def ai_prompt(result):
        base = original(result)
        return (
            base
            + "\n\nREFERENCE SIGNAL METHOD: Evaluate the six technical confirmations "
              "shown in this snapshot: Parabolic SAR, SMA4/60 moving-average "
              "crossover/trend, EMA9/21 moving-average crossover/trend, Donchian20 "
              "breakout/structure, MACD12/26/9 crossover/momentum, and ROC9 "
              "crossover/momentum. Confirm only when the majority (preferably 4/6 "
              "or better) supports the same direction. Reject mixed or contradictory "
              "snapshots. The technical direction in the snapshot is authoritative; "
              "do not change it without evidence from the supplied values."
        )

    a.ai_prompt = ai_prompt
    a._REFERENCE_AI_PROMPT = True
    a.log.warning("REFERENCE AI CONFIRMATION PROMPT ACTIVE")
    return True


def _boot():
    for _ in range(1800):
        try:
            if _install():
                return
        except Exception:
            pass
        time.sleep(0.1)


threading.Thread(target=_boot, name="reference-ai-prompt-hotfix", daemon=True).start()

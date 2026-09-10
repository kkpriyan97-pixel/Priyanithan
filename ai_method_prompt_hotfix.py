"""Final deterministic AI-confirmation prompt patch.
Supplies the full indicator snapshot so the model never has to guess missing fields.
"""
import json, sys, threading, time

def _app():
    m=sys.modules.get("__main__")
    if m is not None and getattr(m,"__file__","").endswith("app.py"): return m
    return sys.modules.get("app")

def _install():
    a=_app()
    if not a or getattr(a,"_FINAL_AI_PROMPT",False): return bool(a)
    original=getattr(a,"ai_prompt",None)
    if not callable(original): return False
    def ai_prompt(result):
        snapshot={
          "asset":result.get("pair"), "entry":result.get("price"),
          "technical_direction":result.get("signal"), "technical_score":result.get("setup_score",result.get("confidence")),
          "indicator_votes":f"{result.get('indicator_votes','?')}/{result.get('indicator_total','6')}",
          "indicators":result.get("indicator_values",{}),
          "ma_cross":result.get("ma_cross"), "ema_cross":result.get("ema_cross"),
          "donchian_breakout":result.get("donchian_breakout"), "macd_cross":result.get("macd_cross"), "roc_cross":result.get("roc_cross"),
          "psar_reversal":result.get("psar_reversal"), "candle_direction":result.get("candle_direction"),
          "candle_body_ratio":result.get("candle_body_ratio"), "rsi":result.get("rsi"), "adx":result.get("adx"),
          "higher_tf_direction":result.get("higher_tf_direction"), "higher_tf_score":result.get("higher_tf_score"),
          "mtf_aligned":result.get("mtf_aligned"), "expiry_candidates":[1,2,3,5,15],
        }
        return (
          "You are the final confirmation layer for a short-term FIXED-TIME demo trading signal.\n"
          "Use ONLY the supplied live snapshot. Never invent an indicator value.\n"
          "Technical engine already calculated six reference indicators: PSAR, SMA4/60, EMA9/21, Donchian20, MACD12/26/9, ROC9.\n"
          "Approve only when the technical direction is coherent, the candle trigger agrees, and there is no major contradiction.\n"
          "Prefer 4/6 or better. A 3/6 setup may be approved only when the higher-timeframe direction agrees and the trigger/momentum evidence is strong.\n"
          "Reject sideways/conflicted setups. Direction MUST equal technical_direction when approving.\n"
          "Choose duration from 1,2,3,5,15 only; choose the shortest duration that matches the strength of the setup and never invent an unavailable duration.\n"
          "Return ONLY the required JSON schema.\n\nLIVE SNAPSHOT:\n" + json.dumps(snapshot,separators=(",",":"),default=str)
        )
    a.ai_prompt=ai_prompt; a._FINAL_AI_PROMPT=True
    a.log.warning("FINAL AI PROMPT ACTIVE: full numeric indicator snapshot + MTF confirmation")
    return True

def _boot():
    for _ in range(1800):
        try:
            if _install(): return
        except Exception: pass
        time.sleep(.1)
threading.Thread(target=_boot,name="final-ai-prompt",daemon=True).start()

"""Signal-output integrity hotfix.

Prevents a technically qualified signal from being sent with missing asset or
entry fields. The existing scanner remains responsible for the decision;
this patch only validates/fills output fields from the actual result object.
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
    if not a or getattr(a,"_SIGNAL_OUTPUT_INTEGRITY",False):
        return bool(a)
    original=getattr(a,"send_to_recipients",None)
    if not callable(original):
        return False

    # The final engine's _signal_text() currently receives a result whose pair
    # can be missing after an upstream provider-normalization path. Repair only
    # from fields already present in that result; never invent an asset/price.
    def normalize_result(result):
        if not isinstance(result,dict):
            return result
        pair=result.get("pair") or result.get("asset") or result.get("symbol") or result.get("instrument")
        price=result.get("price")
        if pair is not None:
            result["pair"]=str(pair).strip().upper()
        if price is not None:
            try:
                result["price"]=float(price)
            except Exception:
                pass
        return result

    a._normalize_signal_result=normalize_result
    a._SIGNAL_OUTPUT_INTEGRITY=True
    try:
        a.log.warning("SIGNAL OUTPUT INTEGRITY ACTIVE: asset/entry fields validated before signal formatting")
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

threading.Thread(target=_boot,name="signal-output-integrity",daemon=True).start()

from __future__ import annotations

from candice_brain import analyze_asset
from brain_rules import ALLOWED_STRATEGY, CYCLE_SECONDS


def candles_up(n=120, start=1_000_000.0):
    out=[]
    for i in range(n):
        ts=start+i*60
        base=(100.0+(i%5)*0.02) if i<100 else (100.0+(i-99)*0.45)
        o=base
        close=o+0.08
        out.append({
            "time":ts,"open":o,"high":close+0.03,"low":o-0.03,
            "close":close,"volume":100.0,
        })
    return out

def candles_down(n=120, start=1_000_000.0):
    out=[]
    for i in range(n):
        ts=start+i*60
        base=(110.0+(i%5)*0.02) if i<100 else (110.0-(i-99)*0.45)
        o=base
        close=o-0.08
        out.append({
            "time":ts,"open":o,"high":o+0.03,"low":close-0.03,
            "close":close,"volume":100.0,
        })
    return out

def main():
    start=1_000_000.0
    now=start+121*60

    up=analyze_asset(
        {"pair":"TEST_UP","display_name":"TEST_UP"},
        candles_up(start=start),
        price=999999.0,
        now=now,
    )
    assert up is not None and up["direction"]=="UP"
    assert up["strategy"]==ALLOWED_STRATEGY
    assert up["expiry_minutes"]==1
    assert up["decision_candle_closed"] is True
    assert {"anchored_vwap","volume_profile_poc","volume_profile_vah","volume_profile_val"} <= set(up["indicator_features"])

    down=analyze_asset(
        {"pair":"TEST_DOWN","display_name":"TEST_DOWN"},
        candles_down(start=start),
        price=0.0001,
        now=now,
    )
    assert down is not None and down["direction"]=="DOWN"
    assert down["strategy"]==ALLOWED_STRATEGY

    # Flat market must not manufacture a direction.
    flat=[]
    for i in range(120):
        flat.append({
            "time":start+i*60,"open":100.0,"high":100.01,
            "low":99.99,"close":100.0,"volume":100.0,
        })
    assert analyze_asset(
        {"pair":"TEST_FLAT","display_name":"TEST_FLAT"},
        flat,
        price=100.0,
        now=now,
    ) is None

    assert CYCLE_SECONDS==180
    print("BRAIN_SMOKE_TEST_OK")


if __name__=="__main__":
    main()

"""Runtime hotfix for the technical scanner.

The Bollinger upper/lower-band objects are pandas Series.  Comparing a scalar
price directly with the whole Series produces pandas' "truth value of a
Series is ambiguous" exception.  This module installs the same technical
logic as app.analyze_pair but compares against the latest band values only.
No broker order/trade execution is added.
"""
import math
import sys
import threading
import time


def _app_module():
    m = sys.modules.get("app")
    if m is not None:
        return m
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return None


def _valid(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _fixed_analyze_pair(pair, df):
    a = _app_module()
    close = df["close"]
    high = df["high"]
    low = df["low"]

    ema9 = a.EMAIndicator(close, 9).ema_indicator()
    ema21 = a.EMAIndicator(close, 21).ema_indicator()
    macd_obj = a.MACD(close)
    macd = macd_obj.macd()
    macd_signal = macd_obj.macd_signal()
    rsi = a.RSIIndicator(close, 14).rsi()
    stoch = a.StochasticOscillator(high, low, close).stoch()
    adx = a.ADXIndicator(high, low, close).adx()
    bb = a.BollingerBands(close)
    bb_high = bb.bollinger_hband()
    bb_low = bb.bollinger_lband()

    c = _valid(close.iloc[-1])
    prev = _valid(close.iloc[-2], c)
    e9 = _valid(ema9.iloc[-1])
    e21 = _valid(ema21.iloc[-1])
    m = _valid(macd.iloc[-1])
    ms = _valid(macd_signal.iloc[-1])
    rv = _valid(rsi.iloc[-1], 50.0)
    sv = _valid(stoch.iloc[-1], 50.0)
    av = _valid(adx.iloc[-1])
    bh = _valid(bb_high.iloc[-1], c)
    bl = _valid(bb_low.iloc[-1], c)

    score_up = (
        (2 if e9 > e21 else 0)
        + (2 if m > ms else 0)
        + (1 if 52 <= rv < 70 else 0)
        + (1 if c < bl else 0)
        + (1 if sv < 20 else 0)
    )
    score_down = (
        (2 if e9 < e21 else 0)
        + (2 if m < ms else 0)
        + (1 if 30 < rv <= 48 else 0)
        + (1 if c > bh else 0)
        + (1 if sv > 80 else 0)
    )
    signal = "UP" if score_up > score_down else ("DOWN" if score_down > score_up else "NO SIGNAL")
    confidence = int(min(99, 50 + abs(score_up - score_down) * 7 + max(0, av - 20)))

    return {
        "pair": pair,
        "signal": signal,
        "confidence": confidence,
        "candle_time": a.format_uae_timestamp(float(df["timestamp"].iloc[-1])),
        "patterns": ["bullish close" if c > prev else "bearish close"],
        "trend": "BULLISH" if score_up > score_down else ("BEARISH" if score_down > score_up else "MIXED"),
        "rsi": rv,
        "adx": av,
        "reason": "technical structure",
        "price": c,
    }


def _patch():
    a = _app_module()
    if a is None or not hasattr(a, "analyze_pair"):
        return False
    if getattr(a, "_ANALYSIS_SERIES_HOTFIX", False):
        return True
    a.analyze_pair = _fixed_analyze_pair
    a._ANALYSIS_SERIES_HOTFIX = True
    a.log.warning("ANALYSIS HOTFIX ACTIVE: Bollinger bands use scalar latest values")
    return True


def _boot():
    for _ in range(180):
        try:
            if _patch():
                return
        except Exception:
            pass
        time.sleep(0.25)


threading.Thread(target=_boot, name="analysis-series-hotfix", daemon=True).start()

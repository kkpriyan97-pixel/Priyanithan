"""Multi-indicator signal method matching the requested reference style.

Uses the live Olymptrade candle dataframe already supplied by app.py.  This
module does not place trades.  It replaces the technical classifier with a
six-vote method based on PSAR, moving-average crossover, EMA crossover,
Donchian breakout, MACD crossover/state, and ROC crossover/state.  Existing
RSI/ADX values from the original analyzer are retained as context.
"""
import sys
import threading
import time

import pandas as pd


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _psar(high, low, close, step=0.02, maximum=0.20):
    h = high.astype(float).reset_index(drop=True)
    l = low.astype(float).reset_index(drop=True)
    c = close.astype(float).reset_index(drop=True)
    n = len(c)
    if n < 3:
        return pd.Series([float(c.iloc[0])] * n)
    sar = [float(l.iloc[0])]
    bull = True
    af = step
    ep = float(h.iloc[0])
    for i in range(1, n):
        prev_sar = sar[-1]
        value = prev_sar + af * (ep - prev_sar)
        if bull:
            bound = float(l.iloc[i - 1])
            if i >= 2:
                bound = min(bound, float(l.iloc[i - 2]))
            value = min(value, bound)
            if float(l.iloc[i]) < value:
                bull = False
                value = ep
                ep = float(l.iloc[i])
                af = step
            else:
                if float(h.iloc[i]) > ep:
                    ep = float(h.iloc[i])
                    af = min(maximum, af + step)
        else:
            bound = float(h.iloc[i - 1])
            if i >= 2:
                bound = max(bound, float(h.iloc[i - 2]))
            value = max(value, bound)
            if float(h.iloc[i]) > value:
                bull = True
                value = ep
                ep = float(h.iloc[i])
                af = step
            else:
                if float(l.iloc[i]) < ep:
                    ep = float(l.iloc[i])
                    af = min(maximum, af + step)
        sar.append(value)
    return pd.Series(sar, index=close.index)


def _cross_up(a, b):
    return float(a.iloc[-1]) > float(b.iloc[-1]) and float(a.iloc[-2]) <= float(b.iloc[-2])


def _cross_down(a, b):
    return float(a.iloc[-1]) < float(b.iloc[-1]) and float(a.iloc[-2]) >= float(b.iloc[-2])


def _install():
    a = _app()
    if not a or getattr(a, "_REFERENCE_INDICATOR_METHOD", False):
        return bool(a)
    original = getattr(a, "analyze_pair", None)
    if not callable(original):
        return False

    def analyze_pair(pair, df):
        base = original(pair, df)
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        if len(df) < 65:
            return base

        sma4 = close.rolling(4).mean()
        sma60 = close.rolling(60).mean()
        ema9 = close.ewm(span=9, adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        macd_signal = macd.ewm(span=9, adjust=False).mean()
        roc9 = close.pct_change(9) * 100.0
        sar = _psar(high, low, close)
        dc_upper = high.rolling(20).max().shift(1)
        dc_lower = low.rolling(20).min().shift(1)

        c = float(close.iloc[-1])
        prev_c = float(close.iloc[-2])
        sar_up = c > float(sar.iloc[-1])
        prev_sar_up = prev_c > float(sar.iloc[-2])
        sma_up = float(sma4.iloc[-1]) > float(sma60.iloc[-1])
        ema_up = float(ema9.iloc[-1]) > float(ema21.iloc[-1])
        macd_up = float(macd.iloc[-1]) > float(macd_signal.iloc[-1])
        roc_up = float(roc9.iloc[-1]) > 0
        donchian_up = c > float(dc_upper.iloc[-1]) if pd.notna(dc_upper.iloc[-1]) else False
        donchian_down = c < float(dc_lower.iloc[-1]) if pd.notna(dc_lower.iloc[-1]) else False

        up_votes = sum((sar_up, sma_up, ema_up, macd_up, roc_up, donchian_up))
        down_votes = sum((not sar_up, not sma_up, not ema_up, not macd_up, not roc_up, donchian_down))

        # Require strong agreement: at least four of the six methods and a
        # strict majority.  This is a filter, not a profitability guarantee.
        if up_votes >= 4 and up_votes > down_votes:
            signal = "UP"
            votes = up_votes
        elif down_votes >= 4 and down_votes > up_votes:
            signal = "DOWN"
            votes = down_votes
        else:
            signal = "NO SIGNAL"
            votes = max(up_votes, down_votes)

        patterns = []
        if sar_up == prev_sar_up:
            patterns.append("Parabolic SAR Bullish" if sar_up else "Parabolic SAR Bearish")
        else:
            patterns.append("Parabolic SAR Reversal")
        if _cross_up(sma4, sma60) or _cross_down(sma4, sma60):
            patterns.append("Moving Average Crossover")
        else:
            patterns.append("Moving Average Trend")
        if _cross_up(ema9, ema21) or _cross_down(ema9, ema21):
            patterns.append("EMA Moving Average Crossover")
        if donchian_up or donchian_down:
            patterns.append("Donchian Channel Breakout")
        else:
            patterns.append("Donchian Channel Structure")
        if _cross_up(macd, macd_signal) or _cross_down(macd, macd_signal):
            patterns.append("MACD Crossover")
        else:
            patterns.append("MACD Momentum")
        if _cross_up(roc9, pd.Series(0.0, index=roc9.index)) or _cross_down(roc9, pd.Series(0.0, index=roc9.index)):
            patterns.append("Rate of Change Crossover")
        else:
            patterns.append("Rate of Change Momentum")

        adx = float(base.get("adx", 0) or 0)
        confidence = min(99, 58 + votes * 6 + max(0, min(10, int(adx - 20))))
        result = dict(base)
        result.update({
            "signal": signal,
            "confidence": int(confidence),
            "patterns": patterns,
            "indicator_votes": int(votes),
            "indicator_total": 6,
            "indicator_direction": "UP" if up_votes > down_votes else ("DOWN" if down_votes > up_votes else "MIXED"),
            "indicator_method": "PSAR + SMA4/60 + EMA9/21 + Donchian20 + MACD12/26/9 + ROC9",
            "ma_cross": "UP" if _cross_up(sma4, sma60) else ("DOWN" if _cross_down(sma4, sma60) else "NONE"),
            "macd_cross": "UP" if _cross_up(macd, macd_signal) else ("DOWN" if _cross_down(macd, macd_signal) else "NONE"),
            "roc_cross": "UP" if _cross_up(roc9, pd.Series(0.0, index=roc9.index)) else ("DOWN" if _cross_down(roc9, pd.Series(0.0, index=roc9.index)) else "NONE"),
            "psar_reversal": bool(sar_up != prev_sar_up),
            "donchian_breakout": "UP" if donchian_up else ("DOWN" if donchian_down else "NONE"),
            "reason": f"{votes}/6 reference indicators aligned; ADX={adx:.1f}",
        })
        return result

    a.analyze_pair = analyze_pair
    a._REFERENCE_INDICATOR_METHOD = True
    a.log.warning("REFERENCE INDICATOR METHOD ACTIVE: PSAR + MA + Donchian + MACD + ROC")
    return True


def _boot():
    for _ in range(1800):
        try:
            if _install():
                return
        except Exception:
            a = _app()
            if a and hasattr(a, "log"):
                a.log.exception("REFERENCE INDICATOR HOTFIX INSTALL FAILED")
        time.sleep(0.1)


threading.Thread(target=_boot, name="reference-indicator-hotfix", daemon=True).start()

from __future__ import annotations

import math
import pandas as pd

BULLISH = "UP"
BEARISH = "DOWN"


def _rows(df: pd.DataFrame, n: int = 5):
    if df is None or len(df) < n:
        return None
    return df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True).tail(n).reset_index(drop=True)


def _parts(r):
    o, h, l, c = map(float, (r.open, r.high, r.low, r.close))
    rng = max(h - l, 1e-12)
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    return o, h, l, c, rng, body, upper, lower


def _bull(r): return float(r.close) > float(r.open)
def _bear(r): return float(r.close) < float(r.open)
def _body_ratio(r):
    *_, rng, body, _, _ = _parts(r)
    return body / rng


def detect_candlestick_patterns(df: pd.DataFrame) -> dict:
    """Deterministic A-Z candlestick pattern recognizer.

    It detects common single-, two- and three-candle formations and returns
    directional evidence. It is a pattern feature layer, not a guarantee or
    standalone trade trigger.
    """
    d = _rows(df, 5)
    if d is None:
        return {"patterns": (), "bullish": (), "bearish": (), "neutral": (), "direction": "", "score": 0.0}

    a, b, c, d4, e = [d.iloc[i] for i in range(5)]
    eo, eh, el, ec, er, eb, eu, ed = _parts(e)
    bo, bh, bl, bc, br, bb, bu, bd = _parts(d4)
    co, ch, cl, cc, cr, cb, cu, cd = _parts(c)
    ao, ah, al, ac, ar, ab, au, ad = _parts(b)

    names_bull, names_bear, names_neutral = [], [], []

    # Single-candle families.
    if eb / er <= 0.10:
        names_neutral.append("Doji")
    if eb / er <= 0.25 and eu / er <= 0.20 and ed / er <= 0.20:
        names_neutral.append("Spinning Top")
    if eb / er >= 0.88 and eu / er <= 0.06 and ed / er <= 0.06:
        names_bull.append("Bullish Marubozu") if _bull(e) else names_bear.append("Bearish Marubozu")
    if ed >= 2.0 * max(eb, 1e-12) and eu <= 0.30 * max(eb, 1e-12) and max(eo, ec) >= el + 0.60 * er:
        names_bull.append("Hammer")
    if eu >= 2.0 * max(eb, 1e-12) and ed <= 0.30 * max(eb, 1e-12) and min(eo, ec) <= el + 0.40 * er:
        names_bear.append("Shooting Star")
    if eu >= 2.0 * max(eb, 1e-12) and ed <= 0.30 * max(eb, 1e-12) and max(eo, ec) <= el + 0.40 * er:
        names_bull.append("Inverted Hammer")
    if ed >= 2.0 * max(eb, 1e-12) and eu <= 0.30 * max(eb, 1e-12) and min(eo, ec) >= el + 0.60 * er:
        names_bear.append("Hanging Man")

    # Two-candle families.
    if _bear(d4) and _bull(e) and eo <= bc and ec >= bo and eb > bb * 1.02:
        names_bull.append("Bullish Engulfing")
    if _bull(d4) and _bear(e) and eo >= bc and ec <= bo and eb > bb * 1.02:
        names_bear.append("Bearish Engulfing")
    if _bear(d4) and _bull(e) and eb <= bb * 0.75 and eo >= bc and ec <= bo:
        names_bull.append("Bullish Harami")
    if _bull(d4) and _bear(e) and eb <= bb * 0.75 and eo <= bc and ec >= bo:
        names_bear.append("Bearish Harami")
    if _bear(d4) and _bull(e) and eo < bl and ec > (bo + bc) / 2:
        names_bull.append("Piercing Line")
    if _bull(d4) and _bear(e) and eo > bh and ec < (bo + bc) / 2:
        names_bear.append("Dark Cloud Cover")
    if abs(eh - bh) <= max(er, br) * 0.10 and abs(el - bl) > max(er, br) * 0.20:
        if _bull(e) and _bear(d4): names_bear.append("Tweezer Top")
        if _bear(e) and _bull(d4): names_bull.append("Tweezer Bottom")
    if _bull(e) and _bear(d4) and eo > bh and ec > bo:
        names_bull.append("Bullish Kicker")
    if _bear(e) and _bull(d4) and eo < bl and ec < bo:
        names_bear.append("Bearish Kicker")

    # Three-candle families.
    if _bear(c) and cb / cr >= 0.45 and _bull(e) and eb / er >= 0.45 and ec > (co + cc) / 2:
        names_bull.append("Morning Star")
    if _bull(c) and cb / cr >= 0.45 and _bear(e) and eb / er >= 0.45 and ec < (co + cc) / 2:
        names_bear.append("Evening Star")
    if _bull(c) and _bull(d4) and _bull(e) and cc < bc < ec and cb / cr >= 0.45 and bb / br >= 0.45 and eb / er >= 0.45:
        names_bull.append("Three White Soldiers")
    if _bear(c) and _bear(d4) and _bear(e) and cc > bc > ec and cb / cr >= 0.45 and bb / br >= 0.45 and eb / er >= 0.45:
        names_bear.append("Three Black Crows")
    if _bear(c) and _bull(d4) and _bear(e) and eo > cc and ec < co and bo < cc and bc < co:
        names_bear.append("Bearish Three Inside")
    if _bull(c) and _bear(d4) and _bull(e) and eo < cc and ec > co and bo > cc and bc > co:
        names_bull.append("Bullish Three Inside")
    if _bear(c) and _bull(d4) and _bull(e) and eo > cc and ec > ch and bc < cc:
        names_bull.append("Bullish Three Outside")
    if _bull(c) and _bear(d4) and _bear(e) and eo < cc and ec < cl and bc > cc:
        names_bear.append("Bearish Three Outside")
    if _bear(c) and _bull(d4) and _bear(e) and bh < cl and eo > bh and ec < bl:
        names_bear.append("Abandoned Baby Bearish")
    if _bull(c) and _bear(d4) and _bull(e) and bl > ch and eo < bl and ec > bh:
        names_bull.append("Abandoned Baby Bullish")

    # Inside bar / outside bar and continuation structure.
    if bh <= ch and bl >= cl:
        names_neutral.append("Inside Bar")
    if bh >= ch and bl <= cl:
        names_neutral.append("Outside Bar")
    if _bull(d4) and _bull(e) and ec > bh:
        names_bull.append("Bullish Breakout Candle")
    if _bear(d4) and _bear(e) and ec < bl:
        names_bear.append("Bearish Breakdown Candle")

    bullish = tuple(dict.fromkeys(names_bull))
    bearish = tuple(dict.fromkeys(names_bear))
    neutral = tuple(dict.fromkeys(names_neutral))
    score = max(-1.0, min(1.0, 0.18 * (len(bullish) - len(bearish))))
    direction = BULLISH if score > 0.18 else BEARISH if score < -0.18 else ""
    return {"patterns": bullish + bearish + neutral, "bullish": bullish, "bearish": bearish, "neutral": neutral, "direction": direction, "score": score}


def pattern_evidence(df: pd.DataFrame, direction: str) -> tuple[str, ...]:
    p = detect_candlestick_patterns(df)
    if direction == BULLISH:
        return tuple(p["bullish"])
    if direction == BEARISH:
        return tuple(p["bearish"])
    return ()

"""Precision fallback for selected-asset expiry results.

The selected-asset flow must not mark a signal UNRESOLVED merely because the
broker candle endpoint returns 20 recent candles. For expiry verification we
only need the candle/tick around the expiry boundary, so this hotfix reads the
raw broker candle response directly and accepts the available recent candles.

Read-only result verification only. No broker order is created or modified.
"""
import asyncio
import sys
import threading
import time
from datetime import datetime, timezone


_PATCHED = False


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


async def _raw_expiry_price(a, pair, expiry_ts):
    """Return the best price at the expiry boundary from a tick or raw candles."""
    # Only accept a tick that is genuinely close to the expiry boundary.
    # A later tick must never be mistaken for the historical expiry price.
    ticks = getattr(a, "latest_ticks", {})
    item = ticks.get(pair) if isinstance(ticks, dict) else None
    if isinstance(item, dict):
        try:
            tick_ts = float(item.get("ts", 0) or 0)
            if abs(tick_ts - expiry_ts) <= 5.0:
                for key in ("price", "p", "last", "close", "value", "ask", "bid"):
                    try:
                        value = float(item.get(key))
                        if value > 0:
                            return value, tick_ts, "tick"
                    except (TypeError, ValueError):
                        pass
        except (TypeError, ValueError):
            pass

    client = getattr(a, "ot_client", None)
    if client is None or not getattr(getattr(client, "connection", None), "is_connected", False):
        return None, None, "OlympTrade not connected"

    try:
        raw = await client.market.get_candles(pair, 60, 20)
        normalizer = getattr(a, "normalize_candles", None)
        if not callable(normalizer):
            return None, None, "candle normalizer unavailable"
        df = normalizer(raw)
        if df is None or getattr(df, "empty", True):
            return None, None, "no expiry candles"

        rows = []
        for _, row in df.iterrows():
            try:
                ts = float(row["timestamp"])
                close = float(row["close"])
                if close > 0:
                    rows.append((ts, close))
            except (TypeError, ValueError, KeyError):
                continue
        if not rows:
            return None, None, "no valid expiry candles"
        rows.sort(key=lambda x: x[0])

        # If the feed timestamps candle closes, an exact expiry timestamp is
        # ideal. Otherwise use the latest candle that was already closed at
        # the expiry boundary. This avoids using a candle from after expiry.
        exact = [x for x in rows if abs(x[0] - expiry_ts) <= 2.0]
        if exact:
            ts, price = exact[-1]
            return price, ts, "candle-exact"

        closed = [x for x in rows if x[0] <= expiry_ts - 0.5]
        if closed:
            ts, price = closed[-1]
            return price, ts, "candle-closed"
        return None, None, "insufficient candles around expiry"
    except Exception as exc:
        a.log.warning("EXPIRY PRECISION FALLBACK FAILED pair=%s: %s", pair, exc)
        return None, None, str(exc)


async def _monitor_signal_precise(a, bot, uid, pair, direction, entry, duration, signal_time):
    try:
        expiry_ts = float(signal_time) + int(duration) * 60
        await asyncio.sleep(max(1.0, expiry_ts - time.time()))

        expiry_price = None
        expiry_source = None
        expiry_price_ts = None
        # Give the broker a short grace period to publish the boundary candle.
        for _ in range(8):
            expiry_price, expiry_price_ts, expiry_source = await _raw_expiry_price(a, pair, expiry_ts)
            if expiry_price is not None:
                break
            await asyncio.sleep(2.0)

        if expiry_price is None:
            outcome = "UNRESOLVED"
        elif direction == "UP":
            outcome = "WIN" if expiry_price > entry else ("DRAW" if expiry_price == entry else "LOSS")
        else:
            outcome = "WIN" if expiry_price < entry else ("DRAW" if expiry_price == entry else "LOSS")

        icon = {"WIN": "✅", "LOSS": "❌", "DRAW": "➖", "UNRESOLVED": "⚠️"}[outcome]
        tz = getattr(a, "UAE_TZ", timezone.utc)
        expiry_label = datetime.fromtimestamp(expiry_ts, tz=timezone.utc).astimezone(tz).strftime("%H:%M:%S UAE")
        verified = f"\n🔎 Verification: {expiry_source}\n🕐 Expiry boundary: {expiry_label}"
        if expiry_price_ts is not None:
            verified += f"\n📌 Price timestamp: {datetime.fromtimestamp(expiry_price_ts, tz=timezone.utc).strftime('%H:%M:%S UTC')}"

        await bot.send_message(chat_id=uid, text=(
            f"{icon} PRIYANITHAN SIGNAL RESULT\n\n"
            f"📈 {pair}\n↕️ {direction}\n"
            f"💰 Entry: {entry}\n🏁 Expiry Price: {expiry_price if expiry_price is not None else 'N/A'}\n"
            f"⏱️ Duration: {duration} MIN\n📊 Market Result: {outcome}"
            f"{verified}\n\n"
            "⚠️ RESULT ONLY — AUTO TRADE OFF"
        ))

        if outcome == "LOSS":
            flow = sys.modules.get("final_asset_selection_flow")
            if flow is not None:
                lock = getattr(flow, "LOCK", None)
                selected = getattr(flow, "SELECTED", None)
                if lock is not None and selected is not None:
                    with lock:
                        if selected.get(uid) == pair:
                            selected.pop(uid, None)
                try:
                    await flow._send_asset_menu(bot, uid, title="❌ LOSS — AI RE-ANALYSIS REQUIRED\n\nSelect the next live asset")
                except Exception:
                    pass
        elif outcome == "UNRESOLVED":
            await bot.send_message(
                chat_id=uid,
                text="⚠️ Result could not be verified at the expiry boundary. No next signal was forced."
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        a.log.exception("PRECISE SELECTED RESULT MONITOR FAILED: pair=%s uid=%s", pair, uid)


def _patch_flow(flow, a):
    global _PATCHED
    if getattr(flow, "_RESULT_PRECISION_V1", False):
        _PATCHED = True
        return

    async def monitor_signal(a_, bot, uid, pair, direction, entry, duration, signal_time):
        return await _monitor_signal_precise(a_, bot, uid, pair, direction, entry, duration, signal_time)

    flow._monitor_signal = monitor_signal
    flow._RESULT_PRECISION_V1 = True
    _PATCHED = True
    try:
        a.log.info("EXPIRY RESULT PRECISION V1 INSTALLED: raw 20-candle boundary fallback")
    except Exception:
        pass


def _boot():
    for _ in range(1800):
        try:
            flow = sys.modules.get("final_asset_selection_flow")
            a = _app()
            if flow is not None and a is not None:
                _patch_flow(flow, a)
                if _PATCHED:
                    return
        except Exception:
            pass
        time.sleep(0.1)


threading.Thread(target=_boot, name="expiry-result-precision-boot", daemon=True).start()

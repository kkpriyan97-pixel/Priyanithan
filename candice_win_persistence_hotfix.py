"""Keep the selected asset after a verified WIN.

The canonical result/recovery monitor may call send_asset_menu after every
completed signal. For a WIN this is undesirable: the same selected asset can
continue into the next eligible signal window. This guard remembers the pair
at signal delivery, restores it after the monitor clears transient state, and
suppresses only the WIN asset-menu transition. LOSS/recovery-failure/unresolved
flows still use the normal asset-selection menu.

No order execution, auto trading, martingale, forced signal, or AI-gate change.
"""
import sys
import threading
import time

PATCHED = False


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _patch():
    global PATCHED
    a = _app()
    if a is None:
        return False
    if getattr(a, "_CANDICE_WIN_PERSISTENCE", False):
        PATCHED = True
        return True
    original_send_signal = getattr(a, "send_signal", None)
    original_send_asset_menu = getattr(a, "send_asset_menu", None)
    if not callable(original_send_signal) or not callable(original_send_asset_menu):
        return False

    last_pair = {}

    async def remembered_send_signal(bot, uid, signal):
        try:
            pair = str(signal.get("pair", "")).strip().upper()
            if pair:
                last_pair[int(uid)] = pair
        except Exception:
            pass
        return await original_send_signal(bot, uid, signal)

    async def guarded_asset_menu(bot, uid, note=None):
        text = str(note or "").strip().lower()
        if "win → select a fresh asset" in text or "recovery win → select a fresh asset" in text:
            pair = last_pair.get(int(uid))
            if pair:
                a.selected_asset[int(uid)] = pair
                a.log.info("CANDICE WIN PERSISTENCE: keeping selected asset uid=%s pair=%s", uid, pair)
                try:
                    await a.send_text(
                        bot,
                        "✅ WIN CONFIRMED\n\n"
                        f"📈 {pair}\n"
                        "🔒 Selected asset remains active.\n"
                        "📡 Candice will continue researching it and use the next eligible signal window.\n\n"
                        "⚠️ MANUAL TRADE ONLY — AUTO TRADE OFF",
                        uid,
                    )
                except Exception:
                    pass
                return True
        return await original_send_asset_menu(bot, uid, note)

    a.send_signal = remembered_send_signal
    a.send_asset_menu = guarded_asset_menu
    a._CANDICE_WIN_PERSISTENCE = True
    a._CANDICE_WIN_LAST_PAIR = last_pair
    a.log.warning("CANDICE WIN PERSISTENCE V1 ACTIVE: verified WIN keeps selected asset")
    PATCHED = True
    return True


def _boot():
    for _ in range(1800):
        try:
            if _patch():
                return
        except Exception:
            pass
        time.sleep(0.2)

threading.Thread(target=_boot, name="candice-win-persistence", daemon=True).start()

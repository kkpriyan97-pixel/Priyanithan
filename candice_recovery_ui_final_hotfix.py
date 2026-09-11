"""Final recovery UX guard.

Keeps the strict one-shot recovery engine intact while making its decision
observable and avoiding an unnecessary asset menu after a recovery WIN.
No forced signals, martingale, broker orders, or weakened gates.
"""
import asyncio
import threading
import time

PATCHED = False


def _app():
    import sys
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


async def _recovery_result_ui(a, bot, uid, signal):
    expiry_ts = float(signal.get("created_at", time.time())) + int(signal["duration"]) * 60
    await asyncio.sleep(max(1.0, expiry_ts - time.time()))
    expiry, expiry_price_ts, source = await a.get_expiry_price(signal["pair"], expiry_ts)
    if expiry is None:
        await a.send_text(bot, f"⚠️ RECOVERY RESULT UNRESOLVED — {signal['pair']}\nNo result was guessed.\n🔒 Selected asset remains active; no new asset is forced.", uid)
        a.active_signal.pop(uid, None)
        return
    result = a.verify_result(float(signal["price"]), expiry, signal["direction"])
    recorder = getattr(a, "record_candice_result", None)
    if callable(recorder):
        try:
            recorder(signal, result, expiry)
        except Exception as exc:
            a.log.warning("CANDICE RESULT MEMORY HOOK FAILED: %s", exc)
    await a.send_text(
        bot,
        "📊 SHORT RECOVERY RESULT\n\n"
        f"📈 {signal['pair']}\n"
        f"{'⬆️' if signal['direction']=='UP' else '⬇️'} {signal['direction']}\n"
        f"💰 Entry: {a.fmt_price(signal['price'])}\n"
        f"🏁 Expiry: {a.fmt_price(expiry)}\n"
        f"⏱️ Duration: {signal['duration']} MIN\n"
        f"🔎 Verification: {source}\n\n"
        f"{'✅' if result=='WIN' else '❌' if result=='LOSS' else '➖'} {result}\n\n"
        "⚠️ RECOVERY IS ONE-SHOT — AUTO TRADE OFF",
        uid,
    )
    a.active_signal.pop(uid, None)
    if result == "WIN":
        # Keep the user's selected pair. The normal signal-session scanner can
        # use the next eligible boundary without forcing a new asset selection.
        a.log.info("RECOVERY WIN: retaining selected asset pair=%s uid=%s", signal["pair"], uid)
        await a.send_text(
            bot,
            f"✅ RECOVERY WIN CONFIRMED\n\n📈 {signal['pair']}\n"
            "🔒 Selected asset remains active.\n"
            "📡 Candice will continue researching it for the next eligible signal window.\n\n"
            "⚠️ MANUAL TRADE ONLY — AUTO TRADE OFF",
            uid,
        )
        return
    # A recovery LOSS ends the one-shot recovery cycle; do not immediately
    # revenge-trade. Return to fresh asset selection.
    a.selected_asset.pop(uid, None)
    await a.send_asset_menu(bot, uid, "❌ Recovery LOSS → recovery cycle ended; select a fresh asset.")


async def _find_recovery_with_reason(a, pair):
    """Wrap the existing strict search and expose its final rejection reason."""
    original = getattr(_MODULE, "_CANDICE_ORIGINAL_FIND_RECOVERY", None)
    if original is None:
        original = getattr(_MODULE, "_find_recovery", None)
    if original is None:
        return None, "Recovery engine unavailable"
    recovery, reason = await original(a, pair)
    a._CANDICE_LAST_RECOVERY_REASON = reason or "no qualified short recovery setup"
    return recovery, reason


_MODULE = None
_ORIGINAL_RECOVERY_RESULT = None


def _patch():
    global PATCHED, _MODULE, _ORIGINAL_RECOVERY_RESULT
    a = _app()
    if a is None:
        return False
    try:
        import loss_recovery_ai_hotfix as m
    except Exception:
        return False
    if not getattr(a, "_LOSS_RECOVERY_AI_V2", False):
        return False
    _MODULE = m
    if not getattr(m, "_CANDICE_ORIGINAL_FIND_RECOVERY", None):
        m._CANDICE_ORIGINAL_FIND_RECOVERY = m._find_recovery
        m._find_recovery = _find_recovery_with_reason
    if not getattr(m, "_CANDICE_RECOVERY_UI_FINAL", False):
        _ORIGINAL_RECOVERY_RESULT = m._recovery_result
        m._recovery_result = _recovery_result_ui
        m._CANDICE_RECOVERY_UI_FINAL = True
    # Replace the generic fallback text with the actual last rejection reason.
    original_menu = getattr(a, "send_asset_menu", None)
    if callable(original_menu) and not getattr(a, "_CANDICE_RECOVERY_MENU_REASON", False):
        async def reason_menu(bot, uid, note=None):
            if note and "No safe short recovery setup" in str(note):
                reason = getattr(a, "_CANDICE_LAST_RECOVERY_REASON", "no qualified short recovery setup")
                note = "⚠️ SHORT RECOVERY REJECTED\n\n🔎 Reason: " + str(reason)[:220] + "\n\n🚫 No forced recovery • No martingale\n\n📊 Select a fresh asset."
            return await original_menu(bot, uid, note)
        a.send_asset_menu = reason_menu
        a._CANDICE_RECOVERY_MENU_REASON = True
    a.log.warning("CANDICE RECOVERY UI FINAL ACTIVE: reason reporting + recovery WIN asset retention")
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

threading.Thread(target=_boot, name="candice-recovery-ui-final", daemon=True).start()

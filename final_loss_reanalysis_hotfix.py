"""AI-ranked next-asset menu after a LOSS.

This is read-only market analysis. It never places an order.
"""
import asyncio
import sys
import threading


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


async def _rank_after_loss(bot, uid, original_menu):
    try:
        import final_asset_selection_flow as flow
        a = _app()
        if not a:
            return await original_menu(bot, uid, title="❌ LOSS — SELECT NEXT LIVE ASSET")
        live = await flow._discover_live_assets(a)
        candidates = []
        for pair in live[:10]:
            try:
                df, err = await a.get_ot_candles(pair, 60, 120)
                if df is None or not flow._fresh(df):
                    continue
                result = a.analyze_pair(pair, df)
                if str(result.get("signal", "NO SIGNAL")).upper() == "NO SIGNAL":
                    continue
                result["pair"] = pair
                candidates.append(result)
            except Exception:
                continue
        candidates.sort(key=lambda x: float(x.get("confidence", 0)), reverse=True)
        ranked = []
        for result in candidates[:3]:
            try:
                prompt = a.ai_prompt(result) + "\n\nThis is post-loss asset selection. Rank this asset only. Do not invent data."
                ai, err = await asyncio.wait_for(asyncio.to_thread(a.call_ai, prompt), timeout=30)
                if ai and not err and str(ai.get("decision", "")).upper() == "APPROVE":
                    ranked.append((int(ai.get("confidence", 0) or 0), result["pair"]))
            except Exception:
                continue
        ranked.sort(reverse=True)
        ordered = [p for _, p in ranked] + [p for p in live if p not in {x[1] for x in ranked}]
        ordered = ordered[:24]
        flow.CHOICES[int(uid)] = ordered
        flow.PAGES[int(uid)] = 0
        markup = flow._button_rows(ordered, 0)
        await bot.send_message(
            chat_id=uid,
            text=(
                "❌ LAST SIGNAL = LOSS\n\n"
                "🧠 AI re-analysed the currently LIVE assets.\n"
                f"🟢 Live candidates: {len(live)}\n"
                f"🤖 AI-approved next candidates: {len(ranked)}\n\n"
                "Select the next asset below.\n"
                "⏱️ After selection: next 5-minute signal cycle."
            ),
            reply_markup=markup,
        )
    except Exception:
        a = _app()
        if a is not None and hasattr(a, "log"):
            a.log.exception("FINAL LOSS RE-ANALYSIS FAILED")
        await original_menu(bot, uid, title="❌ LOSS — SELECT NEXT LIVE ASSET")


def install():
    try:
        import final_asset_selection_flow as flow
    except Exception:
        return False
    if getattr(flow, "_LOSS_AI_PATCHED", False):
        return True
    original = flow._send_asset_menu

    async def wrapped(bot, uid, edit_message=None, page=0, title=None):
        if title and "LOSS" in str(title).upper():
            if edit_message is not None:
                try:
                    await edit_message.edit_text("🧠 AI re-analysing currently live assets after the LOSS...")
                except Exception:
                    pass
            return await _rank_after_loss(bot, uid, original)
        return await original(bot, uid, edit_message, page, title)

    flow._send_asset_menu = wrapped
    flow._LOSS_AI_PATCHED = True
    a = _app()
    if a is not None and hasattr(a, "log"):
        a.log.warning("FINAL LOSS AI RE-ANALYSIS ACTIVE")
    return True


def _boot():
    for _ in range(1800):
        try:
            if install():
                return
        except Exception:
            pass
        import time
        time.sleep(0.25)

threading.Thread(target=_boot, name="final-loss-ai-reanalysis", daemon=True).start()

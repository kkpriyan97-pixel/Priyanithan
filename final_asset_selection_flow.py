"""Final Telegram asset-selection and selected-market signal flow.

Replaces the old Trade Now/deep-link flow with a Telegram button selector.
Only assets that return fresh OlympTrade candles are offered. The selected
asset is scanned every 5 minutes; AI chooses the direction and a duration of
2/3/5/10/15 minutes. No broker order is placed and no login is automated.
"""
import asyncio
import re
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler

ALLOWED_DURATIONS = (2, 3, 5, 10, 15)
LIVE_MAX_AGE = 90.0
PAGE_SIZE = 12
UAE = ZoneInfo("Asia/Dubai")
LOCK = threading.RLock()
SELECTED = {}
CHOICES = {}
PAGES = {}
TASKS = {}
INSTALLED = False


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _fresh(df):
    try:
        ts = float(df["timestamp"].iloc[-1])
        if ts > 100000000000:
            ts /= 1000.0
        return max(0.0, time.time() - ts) <= LIVE_MAX_AGE
    except Exception:
        return False


def _authorized(a, uid):
    try:
        return int(uid) in {int(x) for x in a.authorized_users}
    except Exception:
        return False


def _button_rows(items, page):
    start = page * PAGE_SIZE
    page_items = items[start:start + PAGE_SIZE]
    rows = []
    for i in range(0, len(page_items), 2):
        row = []
        for pair in page_items[i:i + 2]:
            row.append(InlineKeyboardButton(pair, callback_data=f"ASSET:{pair}"))
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ PREVIOUS", callback_data=f"ASSET_PAGE:{page-1}"))
    if start + PAGE_SIZE < len(items):
        nav.append(InlineKeyboardButton("NEXT ➡️", callback_data=f"ASSET_PAGE:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔄 REFRESH LIVE ASSETS", callback_data="ASSET_REFRESH")])
    return InlineKeyboardMarkup(rows)


async def _discover_live_assets(a):
    pairs = set()
    try:
        import signal_engine
        universe = await signal_engine._refresh_universe(a)
        pairs.update(universe)
    except Exception:
        pairs.update(getattr(a, "discovered_assets", {}).keys())
    pairs.update(str(x).upper() for x in getattr(a, "PAIRS", []) if x)
    pairs.update(str(x).upper() for x in getattr(a, "MANUAL_PAIRS", []) if x)
    pairs = sorted(p for p in pairs if re.fullmatch(r"[A-Z][A-Z0-9_.-]{1,29}", str(p)))
    sem = asyncio.Semaphore(8)
    live = []

    async def check(pair):
        async with sem:
            try:
                df, err = await asyncio.wait_for(a.get_ot_candles(pair, 60, 5), timeout=10)
                if df is not None and not getattr(df, "empty", True) and _fresh(df):
                    return pair
            except Exception:
                pass
        return None

    found = await asyncio.gather(*(check(p) for p in pairs))
    live = sorted({p for p in found if p})
    a.log.info("FINAL ASSET SELECTOR: live assets=%s/%s max_age=%.0fs", len(live), len(pairs), LIVE_MAX_AGE)
    return live


async def _send_asset_menu(bot, uid, edit_message=None, page=0, title=None):
    a = _app()
    if not a or not _authorized(a, uid):
        return False
    live = await _discover_live_assets(a)
    with LOCK:
        CHOICES[int(uid)] = live
        PAGES[int(uid)] = max(0, int(page))
    if not live:
        text = (
            "⚠️ NO LIVE ASSETS AVAILABLE\n\n"
            "OlympTrade did not return a fresh candle for any discovered asset.\n"
            "The bot will NOT offer closed/stale assets.\n\n"
            "Tap REFRESH and try again."
        )
    else:
        selected = SELECTED.get(int(uid))
        text = (
            f"{title or '📊 SELECT A LIVE OLYMPTRADE ASSET'}\n\n"
            f"🟢 Live assets: {len(live)}\n"
            f"⏱️ Candle freshness: ≤ {int(LIVE_MAX_AGE)}s\n"
            "\nChoose the asset you want AI to analyse.\n"
            "After selection, the next 5-minute cycle starts.\n\n"
            f"Current: {selected or 'NONE'}"
        )
    markup = _button_rows(live, max(0, int(page))) if live else InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 REFRESH LIVE ASSETS", callback_data="ASSET_REFRESH")]
    ])
    if edit_message is not None:
        try:
            await edit_message.edit_text(text, reply_markup=markup)
            return True
        except Exception:
            pass
    await bot.send_message(chat_id=uid, text=text, reply_markup=markup)
    return True


async def _select_callback(update, context):
    q = update.callback_query
    await q.answer()
    a = _app()
    uid = int(q.from_user.id)
    if not a or not _authorized(a, uid):
        await q.edit_message_text("❌ Access required. Use /access first.")
        return
    data = str(q.data or "")
    if data == "ASSET_REFRESH":
        await _send_asset_menu(q.message.get_bot(), uid, q.message, PAGES.get(uid, 0), "🔄 REFRESHED LIVE ASSET LIST")
        return
    if data.startswith("ASSET_PAGE:"):
        try:
            page = max(0, int(data.split(":", 1)[1]))
        except Exception:
            page = 0
        await _send_asset_menu(q.message.get_bot(), uid, q.message, page)
        return
    if not data.startswith("ASSET:"):
        return
    pair = data.split(":", 1)[1].strip().upper()
    live = CHOICES.get(uid, [])
    if pair not in live:
        await _send_asset_menu(q.message.get_bot(), uid, q.message, 0, "⚠️ ASSET LIST EXPIRED — REFRESHED")
        return
    # One final live-candle check prevents a market that closed between menu
    # generation and selection from becoming the active market.
    try:
        df, err = await a.get_ot_candles(pair, 60, 5)
        if df is None or not _fresh(df):
            await _send_asset_menu(q.message.get_bot(), uid, q.message, 0, f"⚠️ {pair} IS NO LONGER LIVE")
            return
    except Exception:
        await _send_asset_menu(q.message.get_bot(), uid, q.message, 0, f"⚠️ {pair} LIVE CHECK FAILED")
        return
    with LOCK:
        SELECTED[uid] = pair
    # The global app scan loop calls scan_cycle on the next 5-minute boundary.
    await q.edit_message_text(
        f"✅ ASSET SELECTED: {pair}\n\n"
        "🟢 LIVE CANDLE VERIFIED\n"
        "🤖 AI analysis: ON\n"
        "⏱️ Next signal window: next 5 minutes\n"
        "📊 Duration: AI chooses 2 / 3 / 5 / 10 / 15 MIN\n\n"
        "After a LOSS, this asset is stopped and Telegram will show a fresh live-asset selection.\n"
        "⚠️ AUTO TRADE: OFF — manual action only."
    )


async def _assets_cmd(update, context):
    a = _app()
    if not a:
        return
    uid = int(update.effective_user.id)
    try:
        a.remember_chat(update)
    except Exception:
        pass
    if not _authorized(a, uid):
        await update.message.reply_text("❌ Not authorized. Use /access YOUR_ACCESS_CODE first.")
        return
    # This final flow is demo/manual only; no Trade Now UI is generated.
    try:
        import fast_manual_entry
        fast_manual_entry.set_mode(uid, "DEMO")
    except Exception:
        pass
    await _send_asset_menu(context.bot, uid)


async def _start_cmd(update, context):
    a = _app()
    if not a:
        return
    uid = int(update.effective_user.id)
    try:
        a.remember_chat(update)
    except Exception:
        pass
    if not _authorized(a, uid):
        await update.message.reply_text("🔐 ACCESS REQUIRED\n\nSend /access YOUR_ACCESS_CODE first.")
        return
    try:
        import fast_manual_entry
        fast_manual_entry.set_mode(uid, "DEMO")
    except Exception:
        pass
    await _send_asset_menu(context.bot, uid)


async def _monitor_signal(a, bot, uid, pair, direction, entry, duration, signal_time):
    try:
        wait = max(1, duration * 60 - int(time.time() - signal_time))
        await asyncio.sleep(wait)
        expiry_price = None
        for attempt in range(5):
            try:
                ticks = getattr(a, "latest_ticks", {})
                item = ticks.get(pair) if isinstance(ticks, dict) else None
                if isinstance(item, dict):
                    for key in ("price", "p", "last", "close", "value", "ask", "bid"):
                        try:
                            value = float(item.get(key))
                            if value > 0:
                                expiry_price = value
                                break
                        except Exception:
                            pass
                if expiry_price is None:
                    df, err = await a.get_ot_candles(pair, 60, 5)
                    if df is not None and not getattr(df, "empty", True) and _fresh(df):
                        expiry_price = float(df["close"].iloc[-1])
            except Exception:
                expiry_price = None
            if expiry_price is not None:
                break
            await asyncio.sleep(5)
        if expiry_price is None:
            outcome = "UNRESOLVED"
        elif direction == "UP":
            outcome = "WIN" if expiry_price > entry else ("DRAW" if expiry_price == entry else "LOSS")
        else:
            outcome = "WIN" if expiry_price < entry else ("DRAW" if expiry_price == entry else "LOSS")
        icon = {"WIN":"✅","LOSS":"❌","DRAW":"➖","UNRESOLVED":"⚠️"}[outcome]
        await bot.send_message(
            chat_id=uid,
            text=(
                f"{icon} PRIYANITHAN SIGNAL RESULT\n\n"
                f"📈 {pair}\n↕️ {direction}\n"
                f"💰 Entry: {entry}\n🏁 Expiry Price: {expiry_price if expiry_price is not None else 'N/A'}\n"
                f"⏱️ Duration: {duration} MIN\n📊 Market Result: {outcome}\n\n"
                "⚠️ RESULT ONLY — AUTO TRADE OFF"
            )
        )
        if outcome == "LOSS":
            with LOCK:
                if SELECTED.get(uid) == pair:
                    SELECTED.pop(uid, None)
            await _send_asset_menu(bot, uid, title="❌ LOSS — AI RE-ANALYSIS REQUIRED\n\nSelect the next live asset")
        elif outcome == "UNRESOLVED":
            await bot.send_message(chat_id=uid, text="⚠️ Result could not be verified from fresh OlympTrade data. No next signal was forced.")
    except asyncio.CancelledError:
        raise
    except Exception:
        a.log.exception("FINAL SELECTED RESULT MONITOR FAILED: pair=%s uid=%s", pair, uid)


async def _scan_selected(a, application):
    if not SELECTED:
        return
    users = list(SELECTED.items())
    for uid, pair in users:
        if not _authorized(a, uid) or SELECTED.get(uid) != pair:
            continue
        try:
            df, err = await a.get_ot_candles(pair, 60, 120)
            if df is None or not _fresh(df):
                await application.bot.send_message(chat_id=uid, text=f"⚠️ {pair} is no longer live. No signal generated.\n\nChoose another live asset:")
                with LOCK:
                    SELECTED.pop(uid, None)
                await _send_asset_menu(application.bot, uid, title="🔄 SELECT A NEW LIVE ASSET")
                continue
            result = a.analyze_pair(pair, df)
            result["pair"] = pair
            try:
                hdf, herr = await a.get_ot_candles(pair, 300, 80)
                if hdf is not None and _fresh(hdf):
                    higher = a.analyze_pair(pair, hdf)
                    result["higher_tf_direction"] = str(higher.get("signal", "NO SIGNAL")).upper()
                    result["higher_tf_score"] = int(higher.get("confidence", higher.get("setup_score", 0)) or 0)
                else:
                    result["higher_tf_direction"] = "UNKNOWN"
            except Exception:
                result["higher_tf_direction"] = "UNKNOWN"
            if str(result.get("signal", "NO SIGNAL")).upper() == "NO SIGNAL":
                await application.bot.send_message(chat_id=uid, text=f"🔎 {pair}\n\nNo qualified technical setup in this 5-minute cycle.\n⏱️ Next scan: 5 minutes.")
                continue
            prompt = a.ai_prompt(result)
            ai, ai_err = await asyncio.wait_for(asyncio.to_thread(a.call_ai, prompt), timeout=35)
            if not ai or ai_err:
                await application.bot.send_message(chat_id=uid, text=f"⚠️ {pair}\n\nAI confirmation unavailable. No signal was forced.\n⏱️ Next scan: 5 minutes.")
                continue
            decision = str(ai.get("decision", "")).upper()
            direction = str(ai.get("direction", "")).upper()
            confidence = int(ai.get("confidence", 0) or 0)
            duration = int(ai.get("duration_min", 5) or 5)
            technical = str(result.get("signal", "NO SIGNAL")).upper()
            if decision != "APPROVE" or direction != technical or direction not in ("UP", "DOWN") or confidence < int(getattr(a, "AI_MIN_CONFIDENCE", 72)) or duration not in ALLOWED_DURATIONS:
                await application.bot.send_message(chat_id=uid, text=f"🚫 {pair}\n\nAI rejected this setup.\n🤖 Decision: {decision or 'MISSING'}\n📊 Confidence: {confidence}%\n⏱️ Candidate duration: {duration} MIN\n\nNo trade signal forced. Next scan: 5 minutes.")
                continue
            entry = float(result.get("price"))
            now = time.time()
            arrow = "⬆️" if direction == "UP" else "⬇️"
            patterns = "\n".join("• " + str(x) for x in (result.get("patterns") or [])[:6])
            await application.bot.send_message(
                chat_id=uid,
                text=(
                    "🔥 PRIYANITHAN AI SIGNAL 🔥\n\n"
                    f"📈 {pair}\n\n{arrow} {direction}\n\n"
                    f"💰 Entry: {entry}\n\n⏱️ Duration: {duration} MIN\n\n"
                    f"🤖 AI Confidence: {confidence}%\n\n"
                    f"📊 Technical Confidence: {int(result.get('confidence', 0))}%\n"
                    f"🧩 1m + 5m Live Market Check\n"
                    f"🕯️ 5m Trend: {result.get('higher_tf_direction', 'UNKNOWN')}\n\n"
                    f"{patterns}\n\n"
                    f"🕐 {datetime.now(UAE).strftime('%H:%M:%S UAE')}\n\n"
                    "🧠 Candice AI: APPROVED\n\n"
                    "⚠️ MANUAL TRADE — AUTO TRADE OFF"
                )
            )
            # Register an independent result monitor. This bypasses the old
            # Trade Now sender completely, so no stale/deep-link UI is attached.
            task = asyncio.create_task(_monitor_signal(a, application.bot, uid, pair, direction, entry, duration, now), name=f"selected-result-{pair}-{uid}")
            TASKS.setdefault(uid, set()).add(task)
            task.add_done_callback(lambda t, u=uid: TASKS.get(u, set()).discard(t))
            a.log.info("FINAL SELECTED SIGNAL: uid=%s pair=%s direction=%s duration=%s confidence=%s", uid, pair, direction, duration, confidence)
        except Exception as exc:
            a.log.exception("FINAL SELECTED ASSET SCAN FAILED: uid=%s pair=%s error=%s", uid, pair, exc)
            await application.bot.send_message(chat_id=uid, text=f"⚠️ {pair}\n\nLive analysis failed safely. No signal was forced.\n⏱️ Next scan: 5 minutes.")


async def _scan_cycle(application):
    a = _app()
    if not a or getattr(a, "_FINAL_SELECTED_FLOW_RUNNING", False):
        return
    a._FINAL_SELECTED_FLOW_RUNNING = True
    try:
        await _scan_selected(a, application)
    finally:
        a._FINAL_SELECTED_FLOW_RUNNING = False


def _patch_start_handler(a, application):
    for group in getattr(application, "handlers", {}).values():
        for handler in group:
            if "start" in getattr(handler, "commands", set()):
                if getattr(handler.callback, "_FINAL_SELECTED_START", False):
                    return
                handler.callback = _start_cmd
                handler.callback._FINAL_SELECTED_START = True
                a.log.info("FINAL SELECTED FLOW: /start now opens live asset buttons; Trade Now removed")
                return


def install():
    global INSTALLED
    a = _app()
    application = getattr(a, "telegram_application", None) if a else None
    if not a or not application:
        return False
    _patch_start_handler(a, application)
    # Replace the old broad scanner with the selected-asset scanner.
    a.scan_cycle = _scan_cycle
    a._FINAL_SELECTED_FLOW = True
    try:
        commands = [getattr(h, "commands", set()) for group in application.handlers.values() for h in group]
        if not any("assets" in c for c in commands):
            application.add_handler(CommandHandler("assets", _assets_cmd))
        if not getattr(application, "_FINAL_ASSET_CALLBACK", False):
            application.add_handler(CallbackQueryHandler(_select_callback, pattern=r"^ASSET(?:[:_])"))
            application._FINAL_ASSET_CALLBACK = True
    except Exception as exc:
        a.log.warning("FINAL ASSET SELECTOR HANDLER INSTALL FAILED: %s", exc)
    INSTALLED = True
    a.log.warning("FINAL SELECTED ASSET FLOW ACTIVE: live-only buttons + 5m selected scan + dynamic 2/3/5/10/15m AI duration + result tracking")
    return True


def _boot():
    for _ in range(1800):
        try:
            if install():
                return
        except Exception:
            a = _app()
            if a is not None and hasattr(a, "log"):
                a.log.exception("FINAL ASSET FLOW BOOTSTRAP RETRY FAILED")
        time.sleep(0.25)

threading.Thread(target=_boot, name="final-selected-asset-flow", daemon=True).start()

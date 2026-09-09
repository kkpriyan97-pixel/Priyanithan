# Priyanithan runtime compatibility patch.
# Keeps broker asset discovery incremental, validates live candles,
# limits AI requests, and uses Telegram webhook delivery to eliminate
# getUpdates polling conflicts between old/new bot instances.
import importlib.abc
import importlib.machinery
import sys

class _Loader(importlib.abc.Loader):
    def __init__(self, original):
        self.original = original

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        source = self.original.get_source(module.__name__)

        # Flask needs request for the custom Telegram webhook endpoint.
        source = source.replace(
            'from flask import Flask\\n',
            'from flask import Flask, request\\n',
        )

        # Keep all incremental broker instrument events instead of replacing
        # the catalogue on every 1054 callback.
        source = source.replace(
            '            discovered_assets.clear()\\n',
            '            # Preserve incremental broker catalogue updates.\\n',
        )

        # Broker auto-discovery is authoritative when OLYMP_PAIRS=AUTO.
        source = source.replace(
            'AUTO_DISCOVER_ASSETS = os.getenv("AUTO_DISCOVER_ASSETS", "true").lower() in ("1", "true", "yes", "on")',
            'AUTO_DISCOVER_ASSETS = True',
        )
        source = source.replace(
            '        if AUTO_DISCOVER_ASSETS and not MANUAL_PAIRS:\\n',
            '        if AUTO_DISCOVER_ASSETS:\\n',
        )

        # Never scan an invented fallback universe. Real broker-discovered
        # instruments are required for signal analysis.
        old_universe = (
            '            universe = MANUAL_PAIRS[:] if MANUAL_PAIRS else PAIRS[:]|'
            '            if AUTO_DISCOVER_ASSETS and discovered_assets:\\n|'
        ).replace('|','\\n')
        new_universe = (
            '            if AUTO_DISCOVER_ASSETS:\\n'
            '                universe = sorted(discovered_assets.keys())\\n'
            '            else:\\n'
            '                universe = MANUAL_PAIRS[:] if MANUAL_PAIRS else PAIRS[:]\\n'
        )
        source = source.replace(old_universe, new_universe)

        # Limit AI to the strongest two candidates per 5-minute cycle.
        source = source.replace(
            'MAX_AI_CANDIDATES = int(os.getenv("MAX_AI_CANDIDATES", "8"))',
            'MAX_AI_CANDIDATES = min(int(os.getenv("MAX_AI_CANDIDATES", "2")), 2)',
        )

        # OpenRouter-only: use exactly the configured model path, without
        # silently fanning out to several additional free models.
        source = source.replace(
            'for m in (OPENROUTER_MODEL, "openrouter/free", "minimax/minimax-m3:free", "google/gemma-4-26b-a4b-it:free"):',
            'for m in (OPENROUTER_MODEL,):',
        )

        # Do not spend a second AI request just because a model returned a
        # malformed decision. normalize_ai_decision safely rejects it.
        retry_block = (
            '                    raw_decision = str(ai.get("decision", "")).strip().upper()\\n'
            '                    if raw_decision not in ("APPROVE", "REJECT"):\\n'
            '                        retry_prompt = ai_prompt(result) + "\\n\\nCRITICAL: Return decision EXACTLY as APPROVE or REJECT."\\n'
            '                        try:\\n'
            '                            retry_ai, retry_err = await asyncio.wait_for(asyncio.to_thread(call_ai, retry_prompt), timeout=90)\\n'
            '                        except asyncio.TimeoutError:\\n'
            '                            retry_ai, retry_err = None, "AI strict-decision retry timed out"\\n'
            '                        ai = retry_ai if not retry_err and retry_ai else normalize_ai_decision(ai)\\n'
            '                    ai = normalize_ai_decision(ai)\\n'
        )
        source = source.replace(
            retry_block,
            '                    ai = normalize_ai_decision(ai)\\n'
        )

        # Reject stale candle responses. A 60-second strategy must not analyze
        # candles materially behind the UAE wall clock.
        old_candle = (
            '        log.info("CANDLE NORMALIZED: pair=%s rows=%s", pair, rows)\\n'
            '        if df is None or rows < 220:\\n'
            '            return None, f"Not enough OlympTrade candles ({rows}/220)"\\n'
            '        log.info("HISTORICAL CANDLES READY: %s rows for %s", rows, pair)\\n'
            '        return df.tail(count).reset_index(drop=True), None\\n'
        )
        new_candle = (
            '        log.info("CANDLE NORMALIZED: pair=%s rows=%s", pair, rows)\\n'
            '        if df is None or rows < 220:\\n'
            '            return None, f"Not enough OlympTrade candles ({rows}/220)"\\n'
            '        now_utc = time.time()\\n'
            '        latest_ts = float(df["timestamp"].iloc[-1])\\n'
            '        # Guard against providers returning millisecond timestamps.\\n'
            '        if latest_ts > 100000000000:\\n'
            '            latest_ts /= 1000.0\\n'
            '            df["timestamp"] = df["timestamp"] / 1000.0\\n'
            '        age_seconds = now_utc - latest_ts\\n'
            '        log.info("LIVE DATA CHECK: pair=%s latest=%s age=%.1fs UAE=%s", pair, format_uae_timestamp(latest_ts), age_seconds, now_uae().strftime("%Y-%m-%d %H:%M:%S %Z"))\\n'
            '        if latest_ts > now_utc + 120:\\n'
            '            return None, f"Future-dated OlympTrade candle rejected (age={age_seconds:.1f}s)"\\n'
            '        if age_seconds > 180:\\n'
            '            return None, f"STALE OlympTrade candles rejected (latest={format_uae_timestamp(latest_ts)}, age={age_seconds:.1f}s)"\\n'
            '        log.info("LIVE CANDLES READY: %s rows for %s; latest=%s; age=%.1fs", rows, pair, format_uae_timestamp(latest_ts), age_seconds)\\n'
            '        return df.tail(count).reset_index(drop=True), None\\n'
        )
        source = source.replace(old_candle, new_candle)

        # Status must report the broker catalogue, not the transient PAIRS
        # value from the first instrument callback.
        source = source.replace(
            'f"📊 Assets: {len(PAIRS)} (auto-discovered)\\n"',
            'f"📊 Assets: {len(discovered_assets) if AUTO_DISCOVER_ASSETS else len(PAIRS)} (live broker catalogue)\\n"',
        )

        # Scanner log should report the actual universe used for this cycle.
        source = source.replace(
            'log.info("OlympTrade connection established. Assets available for scanner: %s", len(PAIRS))',
            'log.info("OlympTrade connection established. Assets available for scanner: %s", len(discovered_assets) if AUTO_DISCOVER_ASSETS else len(PAIRS))',
        )

        # Add a real Telegram webhook endpoint to the existing Flask server.
        # It queues updates into python-telegram-bot's running event loop.
        webhook_globals = (
            'runtime_loop = None\\n'
            'latest_candles = {}\\n'
        )
        webhook_globals_new = (
            'runtime_loop = None\\n'
            'telegram_application = None\\n'
            'latest_candles = {}\\n'
        )
        source = source.replace(webhook_globals, webhook_globals_new)

        webhook_anchor = (
            '@app.get("/health")\\n'
            'def health():\\n'
            '    return "OK"\\n\\n'
        )
        webhook_code = (
            '@app.get("/health")\\n'
            'def health():\\n'
            '    return "OK"\\n\\n'
            '@app.post("/telegram/webhook")\\n'
            'def telegram_webhook():\\n'
            '    """Receive Telegram updates without getUpdates polling conflicts."""\\n'
            '    if telegram_application is None or runtime_loop is None:\\n'
            '        return "Bot is starting", 503\\n'
            '    payload = request.get_json(silent=True)\\n'
            '    if not isinstance(payload, dict):\\n'
            '        return "Bad Request", 400\\n'
            '    try:\\n'
            '        update = Update.de_json(payload, telegram_application.bot)\\n'
            '        future = asyncio.run_coroutine_threadsafe(telegram_application.update_queue.put(update), runtime_loop)\\n'
            '        future.result(timeout=5)\\n'
            '        return "OK", 200\\n'
            '    except Exception as e:\\n'
            '        log.warning("Telegram webhook update failed: %s", e)\\n'
            '        return "Webhook processing failed", 500\\n\\n'
        )
        source = source.replace(webhook_anchor, webhook_code)

        # Switch Telegram from long polling to webhook. setWebhook is mutually
        # exclusive with getUpdates, so this also neutralizes stale pollers using
        # the same token once the new deployment becomes active.
        source = source.replace(
            'async def telegram_runtime(application):\\n'
            '    global runtime_loop\\n'
            '    runtime_loop = asyncio.get_running_loop()\\n',
            'async def telegram_runtime(application):\\n'
            '    global runtime_loop, telegram_application\\n'
            '    runtime_loop = asyncio.get_running_loop()\\n'
            '    telegram_application = application\\n',
        )
        polling_block = (
            '        await application.updater.start_polling(drop_pending_updates=True)\\n'
            '        log.info("Telegram polling started; background bot tasks are running.")\\n'
            '        await asyncio.Event().wait()\\n'
        )
        webhook_block = (
            '        webhook_base = os.getenv("RENDER_EXTERNAL_URL", "https://priyanithan-ai.onrender.com").rstrip("/")\\n'
            '        webhook_url = f"{webhook_base}/telegram/webhook"\\n'
            '        await application.bot.set_webhook(url=webhook_url, drop_pending_updates=True, allowed_updates=["message"])\\n'
            '        log.info("Telegram webhook active: %s", webhook_url)\\n'
            '        await asyncio.Event().wait()\\n'
        )
        source = source.replace(polling_block, webhook_block)

        # Webhook mode does not need the Updater. Keep cleanup safe if an old
        # Updater object exists in the application.
        source = source.replace(
            '        if application.updater and application.updater.running: await application.updater.stop()\\n',
            '        if application.updater and application.updater.running:\\n'
            '            await application.updater.stop()\\n'
        )

        source = source.replace(
            'APP_VERSION = "5.0-flex-adaptive-1-2-3-5-10-15"',
            'APP_VERSION = "6.0-live-uae-webhook"',
        )

        exec(compile(source, self.original.path, "exec"), module.__dict__)
        logging = __import__("logging")
        logging.getLogger("priyanithan").warning(
            "RUNTIME PATCH ACTIVE: live asset catalogue + live candle validation + OpenRouter single-model + Telegram webhook"
        )

class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "app":
            return None
        try:
            sys.meta_path.remove(self)
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _Loader(spec.loader)
        return spec

sys.meta_path.insert(0, _Finder())

# ============================================================
# READ-ONLY SIGNAL RESULT + SEND-TIME CLOCK PATCH
# ============================================================
# app.py is normally executed as __main__ by Render, so the import finder above
# is not enough for this layer. A tiny background installer waits for app.py to
# finish defining its functions, then wraps only the Telegram signal sender.
import re as _rm_re
import time as _rm_time
import threading as _rm_threading
from datetime import datetime as _rm_datetime
from zoneinfo import ZoneInfo as _rm_ZoneInfo

_RM_UAE = _rm_ZoneInfo("Asia/Dubai")
_RM_SIGNAL_RE = _rm_re.compile(
    r"🔥\s*PRIYANITHAN AI SIGNAL\s*🔥.*?"
    r"📈\s*([A-Z0-9_]+).*?"
    r"(?:⬆️|⬇️)\s*(UP|DOWN).*?"
    r"💰\s*Entry:\s*([0-9.]+).*?"
    r"⏱️\s*Expiry:\s*(1|2|3|5|10|15)\s*MIN",
    _rm_re.S,
)
_RM_TIME_RE = _rm_re.compile(r"🕐\s*[^\n]*UAE")


def _install_result_monitor():
    try:
        from trade_result_monitor import register_signal, monitor_signal
    except Exception:
        return False

    module = sys.modules.get("__main__")
    if module is None or not getattr(module, "__file__", "").endswith("app.py"):
        module = sys.modules.get("app")
    if module is None:
        return False
    if getattr(module, "_RESULT_MONITOR_INSTALLED", False):
        return True

    original_format_signal = getattr(module, "format_signal", None)
    original_send = getattr(module, "send_to_recipients", None)
    if original_format_signal is None or original_send is None:
        return False

    module.pending_signals = getattr(module, "pending_signals", {})
    module.pending_signal_tasks = getattr(module, "pending_signal_tasks", set())

    def patched_format_signal(result, ai):
        text = original_format_signal(result, ai)
        # The candle timestamp may be older than the actual alert delivery.
        # Show the real alert/send time in Telegram.
        now_text = _rm_datetime.now(_RM_UAE).strftime("%H:%M:%S UAE")
        return _RM_TIME_RE.sub("🕐 " + now_text, text, count=1)

    module.format_signal = patched_format_signal

    async def get_expiry_price(pair):
        # Prefer a current live tick when the broker callback supplied one.
        ticks = getattr(module, "latest_ticks", {})
        item = ticks.get(str(pair).upper()) if isinstance(ticks, dict) else None
        if isinstance(item, dict):
            for key in ("price", "p", "last", "close", "value", "ask", "bid"):
                value = item.get(key)
                try:
                    if value is not None and float(value) > 0:
                        return float(value)
                except (TypeError, ValueError):
                    pass

        # Safe fallback: use the existing live candle reader, which rejects stale data.
        getter = getattr(module, "get_ot_candles", None)
        if getter is None:
            return None
        df, err = await getter(pair, 60, 120)
        if df is None or getattr(df, "empty", True):
            module.log.warning("SIGNAL RESULT PRICE UNAVAILABLE: pair=%s reason=%s", pair, err)
            return None
        try:
            return float(df["close"].iloc[-1])
        except Exception:
            return None

    async def send_result(signal, outcome):
        icon = {"WIN": "✅", "LOSS": "❌", "DRAW": "➖", "UNRESOLVED": "⚠️"}.get(outcome, "⚠️")
        expiry_price = signal.get("expiry_price", "N/A")
        text = (
            f"{icon} PRIYANITHAN SIGNAL RESULT\n\n"
            f"📈 {signal['pair']}\n"
            f"↕️ {signal['direction']}\n"
            f"💰 Entry: {signal['entry']}\n"
            f"🏁 Expiry Price: {expiry_price}\n"
            f"⏱️ Expiry: {signal['expiry_min']} MIN\n"
            f"📊 Market Result: {outcome}\n\n"
            f"⚠️ SIGNAL RESULT ONLY — MANUAL TRADE / AUTO TRADE OFF"
        )
        sent = await original_send(module.telegram_application.bot, text)
        module.log.info(
            "SIGNAL RESULT SENT: pair=%s outcome=%s sent=%s entry=%s expiry_price=%s",
            signal.get("pair"), outcome, sent, signal.get("entry"), expiry_price,
        )

    async def monitor_one(signal):
        try:
            return await monitor_signal(signal, get_expiry_price, send_result)
        except Exception:
            module.log.exception("SIGNAL RESULT MONITOR ERROR: pair=%s", signal.get("pair"))
            signal["status"] = "UNRESOLVED"
            return "UNRESOLVED"

    async def patched_send(bot, text):
        sent = await original_send(bot, text)
        if sent:
            match = _RM_SIGNAL_RE.search(str(text))
            if match:
                pair, direction, entry_text, expiry_text = match.groups()
                try:
                    entry = float(entry_text)
                    expiry = int(expiry_text)
                    now_ts = _rm_time.time()
                    result = {
                        "pair": pair,
                        "price": entry,
                        "candle_time": _rm_datetime.now(_RM_UAE).strftime("%H:%M:%S UAE"),
                    }
                    ai = {"direction": direction, "duration_min": expiry}
                    signal_id = register_signal(module.pending_signals, result, ai, signal_time=now_ts)
                    signal = module.pending_signals[signal_id]
                    task = asyncio.create_task(monitor_one(signal))
                    module.pending_signal_tasks.add(task)
                    task.add_done_callback(module.pending_signal_tasks.discard)
                    module.log.info(
                        "SIGNAL RESULT MONITOR STARTED: pair=%s direction=%s expiry=%s min signal_time=%s",
                        pair, direction, expiry, _rm_datetime.now(_RM_UAE).strftime("%H:%M:%S UAE"),
                    )
                except Exception:
                    module.log.exception("SIGNAL RESULT MONITOR REGISTER FAILED")
        return sent

    module.send_to_recipients = patched_send
    module._RESULT_MONITOR_INSTALLED = True
    module.log.info("READ-ONLY SIGNAL RESULT MONITOR INSTALLED")
    return True


def _result_monitor_bootstrap():
    for _ in range(300):
        if _install_result_monitor():
            return
        _rm_time.sleep(0.1)

_rm_threading.Thread(target=_result_monitor_bootstrap, name="result-monitor-install", daemon=True).start()

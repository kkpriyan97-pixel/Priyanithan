# Priyanithan runtime compatibility patch.
# Keeps broker asset discovery incremental, validates live candles,
# reviews a broader set of technical candidates with AI, and uses
# Telegram webhook delivery to eliminate getUpdates polling conflicts.
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
        source = source.replace('from flask import Flask\\n', 'from flask import Flask, request\\n')
        source = source.replace('            discovered_assets.clear()\\n', '            # Preserve incremental broker catalogue updates.\\n')
        source = source.replace(
            'AUTO_DISCOVER_ASSETS = os.getenv("AUTO_DISCOVER_ASSETS", "true").lower() in ("1", "true", "yes", "on")',
            'AUTO_DISCOVER_ASSETS = True',
        )
        source = source.replace(
            '        if AUTO_DISCOVER_ASSETS and not MANUAL_PAIRS:\\n',
            '        if AUTO_DISCOVER_ASSETS:\\n',
        )
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
        source = source.replace(
            'MAX_AI_CANDIDATES = int(os.getenv("MAX_AI_CANDIDATES", "8"))',
            'MAX_AI_CANDIDATES = 8',
        )
        source = source.replace(
            'MAX_AI_CANDIDATES = min(int(os.getenv("MAX_AI_CANDIDATES", "2")), 2)',
            'MAX_AI_CANDIDATES = 8',
        )
        source = source.replace(
            'MAX_AI_CANDIDATES = min(int(os.getenv("MAX_AI_CANDIDATES", "5")), 5)',
            'MAX_AI_CANDIDATES = 8',
        )
        source = source.replace(
            'for m in (OPENROUTER_MODEL, "openrouter/free", "minimax/minimax-m3:free", "google/gemma-4-26b-a4b-it:free"):',
            'for m in (OPENROUTER_MODEL,):',
        )
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
        source = source.replace(retry_block, '                    ai = normalize_ai_decision(ai)\\n')
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
        source = source.replace(
            'f"📊 Assets: {len(PAIRS)} (auto-discovered)\\n"',
            'f"📊 Assets: {len(discovered_assets) if AUTO_DISCOVER_ASSETS else len(PAIRS)} (live broker catalogue)\\n"',
        )
        source = source.replace(
            'log.info("OlympTrade connection established. Assets available for scanner: %s", len(PAIRS))',
            'log.info("OlympTrade connection established. Assets available for scanner: %s", len(discovered_assets) if AUTO_DISCOVER_ASSETS else len(PAIRS))',
        )
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
            '        await application.bot.set_webhook(url=webhook_url, drop_pending_updates=True, allowed_updates=["message", "callback_query"])\\n'
            '        log.info("Telegram webhook active: %s", webhook_url)\\n'
            '        await asyncio.Event().wait()\\n'
        )
        source = source.replace(polling_block, webhook_block)
        source = source.replace(
            '        if application.updater and application.updater.running: await application.updater.stop()\\n',
            '        if application.updater and application.updater.running:\\n'
            '            await application.updater.stop()\\n'
        )
        source = source.replace('APP_VERSION = "5.0-flex-adaptive-1-2-3-5-10-15"', 'APP_VERSION = "6.0-live-uae-webhook"')
        exec(compile(source, self.original.path, "exec"), module.__dict__)
        logging = __import__("logging")
        logging.getLogger("priyanithan").warning(
            "RUNTIME PATCH ACTIVE: live asset catalogue + live candle validation + expanded AI review + Telegram webhook"
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
import re as _rm_re
import threading as _rm_threading
import time as _rm_time
from datetime import datetime as _rm_datetime
from zoneinfo import ZoneInfo as _rm_ZoneInfo

_RM_UAE = _rm_ZoneInfo("Asia/Dubai")
_RM_TIME_RE = _rm_re.compile(r"🕐\s*[^\n]*UAE")


def _install_result_clock_patch():
    module = sys.modules.get("__main__")
    if module is None or not getattr(module, "__file__", "").endswith("app.py"):
        module = sys.modules.get("app")
    if module is None or getattr(module, "_RESULT_CLOCK_PATCH_INSTALLED", False):
        return False
    original_format_signal = getattr(module, "format_signal", None)
    if original_format_signal is None:
        return False

    def patched_format_signal(result, ai):
        text = original_format_signal(result, ai)
        now_text = _rm_datetime.now(_RM_UAE).strftime("%H:%M:%S UAE")
        return _RM_TIME_RE.sub("🕐 " + now_text, text, count=1)

    module.format_signal = patched_format_signal
    module._RESULT_CLOCK_PATCH_INSTALLED = True
    module.log.info("RESULT CLOCK PATCH INSTALLED; outcome monitor delegated to trade_result_monitor.py")
    return True


def _bootstrap_result_clock():
    for _ in range(1800):
        if _install_result_clock_patch():
            return
        _rm_time.sleep(0.1)


def _bootstrap_result_monitor_module():
    for _ in range(1800):
        try:
            import trade_result_monitor  # noqa: F401
            module = sys.modules.get("__main__")
            if module is None or not getattr(module, "__file__", "").endswith("app.py"):
                module = sys.modules.get("app")
            if module is not None and getattr(module, "_RESULT_MONITOR_INSTALLED", False):
                return
        except Exception:
            module = sys.modules.get("__main__") or sys.modules.get("app")
            if module is not None and hasattr(module, "log"):
                module.log.exception("RESULT MONITOR BOOTSTRAP IMPORT FAILED")
        _rm_time.sleep(0.1)


_rm_threading.Thread(target=_bootstrap_result_clock, name="signal-clock-bootstrap", daemon=True).start()
_rm_threading.Thread(target=_bootstrap_result_monitor_module, name="signal-result-monitor-bootstrap", daemon=True).start()

try:
    import fast_manual_entry  # noqa: F401
except Exception:
    import logging as _fme_logging
    _fme_logging.getLogger("priyanithan").exception("FAST MANUAL ENTRY IMPORT FAILED")

try:
    import final_asset_selection_flow  # noqa: F401
except Exception:
    import logging as _fas_logging
    _fas_logging.getLogger("priyanithan").exception("FINAL ASSET SELECTION FLOW IMPORT FAILED")

# Priyanithan runtime compatibility patch.
# Keeps broker asset discovery incremental, forces live-data validation,
# and limits AI requests so the OpenRouter free-model quota is not exhausted.
import importlib.abc
import importlib.machinery
import sys

FALLBACK_ASSETS = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
    "EURGBP", "EURJPY", "GBPJPY", "XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD",
    "LTCUSD", "CRASH_300_X", "BOOM_300_X", "ASIA_X",
]

class _Loader(importlib.abc.Loader):
    def __init__(self, original):
        self.original = original

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        source = self.original.get_source(module.__name__)

        # Keep all incremental broker instrument events instead of replacing
        # the catalogue on every 1054 callback.
        source = source.replace(
            "            discovered_assets.clear()\n",
            "            # Preserve incremental broker catalogue updates.\n",
        )

        # Broker auto-discovery is authoritative when OLYMP_PAIRS=AUTO.
        source = source.replace(
            'AUTO_DISCOVER_ASSETS = os.getenv("AUTO_DISCOVER_ASSETS", "true").lower() in ("1", "true", "yes", "on")',
            "AUTO_DISCOVER_ASSETS = True",
        )
        source = source.replace(
            "        if AUTO_DISCOVER_ASSETS and not MANUAL_PAIRS:\n",
            "        if AUTO_DISCOVER_ASSETS:\n",
        )

        # Never scan an invented fallback universe. Real broker-discovered
        # instruments are required for signal analysis.
        old_universe = (
            "            universe = MANUAL_PAIRS[:] if MANUAL_PAIRS else PAIRS[:]\n"
            "            if AUTO_DISCOVER_ASSETS and discovered_assets:\n"
            "                universe = sorted(discovered_assets.keys())\n"
        )
        new_universe = (
            "            if AUTO_DISCOVER_ASSETS:\n"
            "                universe = sorted(discovered_assets.keys())\n"
            "            else:\n"
            "                universe = MANUAL_PAIRS[:] if MANUAL_PAIRS else PAIRS[:]\n"
        )
        source = source.replace(old_universe, new_universe)

        # Limit AI to the strongest two candidates per 5-minute cycle.
        source = source.replace(
            'MAX_AI_CANDIDATES = int(os.getenv("MAX_AI_CANDIDATES", "8"))',
            'MAX_AI_CANDIDATES = min(int(os.getenv("MAX_AI_CANDIDATES", "2")), 2)',
        )

        # OpenRouter-only: one selected model per candidate, no fan-out retries.
        source = source.replace(
            'for m in (OPENROUTER_MODEL, "openrouter/free", "minimax/minimax-m3:free", "google/gemma-4-26b-a4b-it:free"):',
            'for m in (OPENROUTER_MODEL,):',
        )

        # Reject stale candle responses. A 60-second strategy must not analyze
        # candles whose newest timestamp is materially behind the UAE wall clock.
        old_candle = (
            '        log.info("CANDLE NORMALIZED: pair=%s rows=%s", pair, rows)\n'
            '        if df is None or rows < 220:\n'
            '            return None, f"Not enough OlympTrade candles ({rows}/220)"\n'
            '        log.info("HISTORICAL CANDLES READY: %s rows for %s", rows, pair)\n'
            '        return df.tail(count).reset_index(drop=True), None\n'
        )
        new_candle = (
            '        log.info("CANDLE NORMALIZED: pair=%s rows=%s", pair, rows)\n'
            '        if df is None or rows < 220:\n'
            '            return None, f"Not enough OlympTrade candles ({rows}/220)"\n'
            '        now_utc = time.time()\n'
            '        latest_ts = float(df["timestamp"].iloc[-1])\n'
            '        age_seconds = now_utc - latest_ts\n'
            '        log.info("LIVE DATA CHECK: pair=%s latest=%s age=%.1fs UAE=%s", pair, format_uae_timestamp(latest_ts), age_seconds, now_uae().strftime("%Y-%m-%d %H:%M:%S %Z"))\n'
            '        # Allow a small transport/provider delay, but never accept stale historical data.\n'
            '        if latest_ts > now_utc + 120:\n'
            '            return None, f"Future-dated OlympTrade candle rejected (age={age_seconds:.1f}s)"\n'
            '        if age_seconds > 180:\n'
            '            return None, f"STALE OlympTrade candles rejected (latest={format_uae_timestamp(latest_ts)}, age={age_seconds:.1f}s)"\n'
            '        log.info("LIVE CANDLES READY: %s rows for %s; latest=%s; age=%.1fs", rows, pair, format_uae_timestamp(latest_ts), age_seconds)\n'
            '        return df.tail(count).reset_index(drop=True), None\n'
        )
        source = source.replace(old_candle, new_candle)

        # Make the runtime report the actual UAE date/time used by the scanner.
        source = source.replace(
            'APP_VERSION = "5.0-flex-adaptive-1-2-3-5-10-15"',
            'APP_VERSION = "5.5-live-uae-data-validated"',
        )

        exec(compile(source, self.original.path, "exec"), module.__dict__)
        logging = __import__("logging")
        logging.getLogger("priyanithan").warning(
            "RUNTIME PATCH ACTIVE: incremental discovery + live candle age validation + OpenRouter single-model path"
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

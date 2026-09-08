# Priyanithan runtime compatibility patch.
# Keeps broker asset discovery incremental and limits AI requests so the
# OpenRouter free-model quota is not exhausted by fallback retries.
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

        # Do not erase the broker catalogue when another incremental event arrives.
        source = source.replace(
            "            discovered_assets.clear()\n",
            "            # Preserve incremental broker catalogue updates.\n",
        )

        # AUTO discovery is authoritative.
        source = source.replace(
            'AUTO_DISCOVER_ASSETS = os.getenv("AUTO_DISCOVER_ASSETS", "true").lower() in ("1", "true", "yes", "on")',
            "AUTO_DISCOVER_ASSETS = True",
        )
        source = source.replace(
            "        if AUTO_DISCOVER_ASSETS and not MANUAL_PAIRS:\n",
            "        if AUTO_DISCOVER_ASSETS:\n",
        )

        # Scanner: use discovered instruments first; retain fallback only when the
        # broker exposes fewer than five instruments at startup.
        old_universe = (
            "            universe = MANUAL_PAIRS[:] if MANUAL_PAIRS else PAIRS[:]\n"
            "            if AUTO_DISCOVER_ASSETS and discovered_assets:\n"
            "                universe = sorted(discovered_assets.keys())\n"
        )
        new_universe = (
            "            if AUTO_DISCOVER_ASSETS:\n"
            "                discovered = sorted(discovered_assets.keys())\n"
            "                universe = discovered if len(discovered) >= 5 else sorted(set(discovered + FALLBACK_ASSETS))\n"
            "            else:\n"
            "                universe = MANUAL_PAIRS[:] if MANUAL_PAIRS else PAIRS[:]\n"
        )
        source = source.replace(old_universe, new_universe)

        # Limit the number of AI candidates per 5-minute cycle to 2. This keeps the
        # free OpenRouter daily quota usable while still validating the strongest
        # technical setups.
        source = source.replace(
            'MAX_AI_CANDIDATES = int(os.getenv("MAX_AI_CANDIDATES", "8"))',
            'MAX_AI_CANDIDATES = min(int(os.getenv("MAX_AI_CANDIDATES", "2")), 2)',
        )

        # OpenRouter-only: do not fan out one candidate across several free models.
        # A 429 should consume at most one request for that candidate.
        source = source.replace(
            'for m in (OPENROUTER_MODEL, "openrouter/free", "minimax/minimax-m3:free", "google/gemma-4-26b-a4b-it:free"):',
            'for m in (OPENROUTER_MODEL,):',
        )

        # Publish each candidate immediately before AI validation.
        old_ai_start = (
            '                    log.info("5-minute AI START: pair=%s score=%.1f", pair, result["scan_score"])\n'
        )
        new_ai_start = (
            '                    log.info("5-minute AI START: pair=%s score=%.1f", pair, result["scan_score"])\n'
            '                    await send_to_recipients(application.bot, f"🔎 LIVE AI SCAN\\n\\n📌 Asset: {pair}\\n📊 Technical direction: {result[\'signal\']}\\n🎯 Technical confidence: {result[\'confidence\']}%\\n📈 Scan score: {result[\'scan_score\']:.1f}\\n🤖 AI status: ANALYZING NOW\\n\\n⏱️ 5-minute cycle")\n'
        )
        source = source.replace(old_ai_start, new_ai_start)

        source = source.replace(
            'APP_VERSION = "5.0-flex-adaptive-1-2-3-5-10-15"',
            'APP_VERSION = "5.4-ai-budgeted-live-scan"',
        )

        exec(compile(source, self.original.path, "exec"), module.__dict__)
        logging = __import__("logging")
        logging.getLogger("priyanithan").warning(
            "RUNTIME PATCH ACTIVE: incremental discovery + AI max 2 candidates/cycle + OpenRouter single-model path"
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

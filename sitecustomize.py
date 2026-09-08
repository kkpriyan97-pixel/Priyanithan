# Priyanithan runtime compatibility patch.
# Keeps broker asset discovery incremental and provides a safe fallback universe
# when the broker only exposes a single instrument in the startup catalogue.
# Also publishes the exact assets reaching AI validation to Telegram.
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

        # AUTO discovery is authoritative. Ignore a stale OLYMP_PAIRS value while
        # this runtime compatibility patch is active.
        source = source.replace(
            'AUTO_DISCOVER_ASSETS = os.getenv("AUTO_DISCOVER_ASSETS", "true").lower() in ("1", "true", "yes", "on")',
            "AUTO_DISCOVER_ASSETS = True",
        )
        source = source.replace(
            "        if AUTO_DISCOVER_ASSETS and not MANUAL_PAIRS:\n",
            "        if AUTO_DISCOVER_ASSETS:\n",
        )

        # Scanner: use all discovered instruments first; if the broker only exposes
        # one startup instrument, use the conservative fallback list so the scanner
        # does not remain permanently stuck at Assets scanned: 1.
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

        # Publish each candidate immediately before AI validation so Telegram shows
        # exactly which asset the AI is analyzing in real time. This is deliberately
        # candidate-only, not every raw asset, to avoid flooding Telegram.
        old_ai_start = (
            '                    log.info("5-minute AI START: pair=%s score=%.1f", pair, result["scan_score"])\n'
        )
        new_ai_start = (
            '                    log.info("5-minute AI START: pair=%s score=%.1f", pair, result["scan_score"])\n'
            '                    await send_to_recipients(application.bot, f"🔎 LIVE AI SCAN\\n\\n📌 Asset: {pair}\\n📊 Technical direction: {result[\'signal\']}\\n🎯 Technical confidence: {result[\'confidence\']}%\\n📈 Scan score: {result[\'scan_score\']:.1f}\\n🤖 AI status: ANALYZING NOW\\n\\n⏱️ 5-minute cycle")\n'
        )
        source = source.replace(old_ai_start, new_ai_start)

        # Runtime marker proves in Render logs that this compatibility layer loaded.
        source = source.replace(
            'APP_VERSION = "5.0-flex-adaptive-1-2-3-5-10-15"',
            'APP_VERSION = "5.3-live-ai-scan-runtime-patch"',
        )

        exec(compile(source, self.original.path, "exec"), module.__dict__)
        logging = __import__("logging")
        logging.getLogger("priyanithan").warning(
            "RUNTIME PATCH ACTIVE: auto discovery + fallback universe + LIVE AI Telegram asset updates (%s fallback assets)",
            len(FALLBACK_ASSETS),
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

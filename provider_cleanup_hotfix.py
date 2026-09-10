"""AI provider safety and fallback cleanup.

The signal engine may retry the existing AI provider chain, but this module
ensures known-invalid legacy providers do not silently consume attempts.
AUTO TRADE remains OFF.
"""
import os

# Disable legacy OpenRouter free routing. It previously returned unavailable
# model errors in this deployment. A paid OpenRouter model can be enabled only
# by explicitly setting OPENROUTER_MODEL to a non-free model.
_openrouter_model = os.getenv("OPENROUTER_MODEL", "").strip().lower()
if not _openrouter_model or _openrouter_model in {
    "openrouter/free", "openai/gpt-oss-120b:free"
}:
    os.environ.pop("OPENROUTER_API_KEY", None)

# Airforce is not part of the supported fallback chain.
os.environ.pop("AIRFORCE_API_KEY", None)
os.environ.pop("AIRFORCE_MODEL", None)

# Keep retry configuration deterministic and bounded.
os.environ.setdefault("AI_PROVIDER_RETRIES", "2")

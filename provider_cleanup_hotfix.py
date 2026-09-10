"""AI provider safety cleanup.

Keeps the existing provider implementation intact while preventing known-invalid
free OpenRouter routing from being selected accidentally. Groq remains available
as the active provider; Cerebras remains an optional fallback when funded.
Airforce is ignored even if an old environment variable is still present.
"""
import os

# The current OpenRouter free route/model used by the older provider patch is
# not a reliable fallback for this deployment. Do not let an old key silently
# activate it. The key can remain in Render temporarily, but it is intentionally
# ignored until a valid paid OpenRouter model is explicitly configured.
if os.getenv("OPENROUTER_MODEL", "").strip().lower() in {
    "", "openrouter/free", "openai/gpt-oss-120b:free"
}:
    os.environ.pop("OPENROUTER_API_KEY", None)

# Airforce was removed from the intended provider chain. Ignore stale secrets.
os.environ.pop("AIRFORCE_API_KEY", None)
os.environ.pop("AIRFORCE_MODEL", None)

"""Resilient multi-provider AI router for the NEXORA/Candice DEMO brain.

Design goals:
- 429s never block the deterministic 5-minute signal scheduler.
- Requests are paced globally to avoid burst limits.
- Retry-After is honored; otherwise bounded exponential backoff + jitter.
- Provider-specific cooldown prevents retry storms.
- Short-lived request deduplication prevents repeated AI calls for the same
  market snapshot across overlapping scans.
- Fallback is attempted only after bounded transient failure; permanent
  configuration/auth errors are not retried.
- The technical Candice brain remains authoritative for direction; AI only
  verifies the brain direction.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from ai_engine import MarketSnapshot, build_ai_request, parse_ai_decision


TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
AI_MAX_CONCURRENT = max(1, int(os.getenv("AI_MAX_CONCURRENT", "2")))
AI_MIN_INTERVAL = max(0.05, float(os.getenv("AI_MIN_INTERVAL_SECONDS", "0.75")))
AI_RETRY_LIMIT = max(0, int(os.getenv("AI_RETRY_LIMIT", "2")))
AI_RETRY_CAP_SECONDS = max(1.0, float(os.getenv("AI_RETRY_CAP_SECONDS", "4")))
AI_PROVIDER_COOLDOWN = max(5.0, float(os.getenv("AI_PROVIDER_COOLDOWN_SECONDS", "30")))
AI_CACHE_TTL = max(5.0, float(os.getenv("AI_CACHE_TTL_SECONDS", "45")))

_GATE = asyncio.Semaphore(AI_MAX_CONCURRENT)
_RATE_LOCK = asyncio.Lock()
_NEXT_SLOT = 0.0
_PROVIDER_COOLDOWN_UNTIL: dict[str, float] = {}
_DECISION_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _providers() -> list[str]:
    names: list[str] = []
    primary = os.getenv("AI_PROVIDER", "OPENAI").strip().upper()
    if primary:
        names.append(primary)
    for n in os.getenv("AI_FALLBACK_PROVIDERS", "OPENROUTER,MISTRAL").split(","):
        n = n.strip().upper()
        if n and n not in names:
            names.append(n)
    return names


def _cfg(name: str):
    key = os.getenv(f"{name}_API_KEY", "").strip()
    if not key and name == "OPENAI":
        key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key and name == "OPENROUTER":
        key = os.getenv("OPENROUTER_API_KEY", "").strip()

    base = os.getenv(f"{name}_BASE_URL", "").strip().rstrip("/")
    if not base:
        base = {
            "OPENAI": "https://api.openai.com/v1",
            "OPENROUTER": "https://openrouter.ai/api/v1",
            "MISTRAL": "https://api.mistral.ai/v1",
        }.get(name, "")

    model = os.getenv(f"{name}_MODEL", "").strip() or os.getenv("AI_MODEL", "").strip()
    if not key or not base or not model:
        return None
    return base, model, key


def _snapshot_key(snapshot: MarketSnapshot) -> str:
    """Stable fingerprint that ignores wall-clock timestamp churn."""
    tail = []
    for c in (snapshot.candles or [])[-6:]:
        if not isinstance(c, dict):
            continue
        tail.append({
            "t": c.get("time", c.get("t")),
            "o": c.get("open", c.get("o")),
            "h": c.get("high", c.get("h")),
            "l": c.get("low", c.get("l")),
            "c": c.get("close", c.get("c")),
        })
    raw = json.dumps(
        {
            "pair": snapshot.pair,
            "display_name": snapshot.display_name,
            "mode": snapshot.mode,
            "profitability": snapshot.profitability,
            "price": snapshot.price,
            "candles": tail,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> dict[str, Any] | None:
    item = _DECISION_CACHE.get(key)
    if not item:
        return None
    expires, value = item
    if time.monotonic() >= expires:
        _DECISION_CACHE.pop(key, None)
        return None
    return dict(value)


def _cache_put(key: str, value: dict[str, Any]) -> None:
    now = time.monotonic()
    _DECISION_CACHE[key] = (now + AI_CACHE_TTL, dict(value))
    # Small bounded cleanup; this cache must never grow with every candle.
    if len(_DECISION_CACHE) > 256:
        for k, (expires, _) in list(_DECISION_CACHE.items()):
            if expires <= now:
                _DECISION_CACHE.pop(k, None)
        while len(_DECISION_CACHE) > 192:
            _DECISION_CACHE.pop(next(iter(_DECISION_CACHE)), None)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


async def _pace() -> None:
    """Global request pacing so multiple scan tasks cannot create a burst."""
    global _NEXT_SLOT
    async with _RATE_LOCK:
        now = time.monotonic()
        wait = max(0.0, _NEXT_SLOT - now)
        _NEXT_SLOT = max(now, _NEXT_SLOT) + AI_MIN_INTERVAL
    if wait:
        await asyncio.sleep(wait)


def _cooldown_active(name: str) -> bool:
    return time.monotonic() < _PROVIDER_COOLDOWN_UNTIL.get(name, 0.0)


def _cooldown(name: str, seconds: float | None = None) -> None:
    _PROVIDER_COOLDOWN_UNTIL[name] = time.monotonic() + (
        AI_PROVIDER_COOLDOWN if seconds is None else max(0.0, seconds)
    )


def _prompt(snapshot: MarketSnapshot) -> str:
    request = build_ai_request(snapshot)
    # The technical brain keeps its full 120-candle history locally. The AI is
    # only a verification layer, so send a compact recent window to reduce
    # token pressure and make 429s less likely without changing brain logic.
    candles = request.get("market", {}).get("candles", [])
    compact = []
    for c in candles[-36:]:
        if not isinstance(c, dict):
            continue
        compact.append({
            "t": c.get("time", c.get("t")),
            "o": c.get("open", c.get("o")),
            "h": c.get("high", c.get("h")),
            "l": c.get("low", c.get("l")),
            "c": c.get("close", c.get("c")),
        })
    request["market"]["candles"] = compact
    return (
        "You are Candice Brain's verification layer. Analyze only supplied live "
        "OHLC/market evidence. Do not invent data. Return JSON only: direction "
        "UP/DOWN or empty, confidence 0-100, reason. This is DEMO read-only; "
        "never trade. The technical brain is authoritative for the final "
        "direction; your job is verification only.\\n"
        + json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    )


async def _call_provider(
    name: str,
    snapshot: MarketSnapshot,
    prompt: str,
) -> dict[str, Any] | None:
    cfg = _cfg(name)
    if not cfg:
        return None

    base, model, key = cfg
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return only JSON with direction (UP/DOWN or empty), "
                    "confidence (0-100), and short reason."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }

    async with _GATE:
        for attempt in range(AI_RETRY_LIMIT + 1):
            await _pace()
            try:
                timeout = httpx.Timeout(4.5, connect=3.0)
                async with httpx.AsyncClient(timeout=timeout) as http:
                    response = await http.post(
                        url, headers=headers, json=payload
                    )

                if response.status_code >= 400:
                    status = response.status_code
                    if status not in TRANSIENT_STATUS:
                        raise RuntimeError(
                            f"{name} permanent HTTP {status}"
                        )

                    retry_after = _retry_after(response)
                    if status == 429:
                        # Stop hammering a provider that explicitly rate-limited us.
                        cooldown = retry_after if retry_after is not None else AI_PROVIDER_COOLDOWN
                        cooldown = min(max(cooldown, 5.0), 120.0)
                        _cooldown(name, cooldown)
                        log_msg = (
                            f"AI_429 provider={name} retry_after="
                            f"{retry_after if retry_after is not None else 'missing'} "
                            f"cooldown={cooldown:.1f}s attempt={attempt+1}"
                        )
                        print(log_msg)
                        # When the provider explicitly asks us to wait longer than
                        # our bounded retry window, do not make another 429-causing
                        # request. Move to fallback immediately.
                        if retry_after is not None and retry_after > AI_RETRY_CAP_SECONDS:
                            raise RuntimeError(f"{name} rate-limited; provider cooldown active")

                    if attempt >= AI_RETRY_LIMIT:
                        raise RuntimeError(f"{name} transient HTTP {status}")

                    if retry_after is not None:
                        delay = min(retry_after, AI_RETRY_CAP_SECONDS)
                    else:
                        delay = min(
                            AI_RETRY_CAP_SECONDS,
                            0.8 * (2 ** attempt),
                        )
                    delay += random.uniform(0.05, 0.35)
                    await asyncio.sleep(delay)
                    continue

                body = response.json()
                content = body["choices"][0]["message"]["content"]
                decision = parse_ai_decision(content, snapshot)
                if decision is None:
                    return None

                return {
                    "decision": "SIGNAL",
                    "direction": decision.direction,
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                    "display_name": decision.display_name,
                    "pair": decision.pair,
                    "provider": name,
                }

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= AI_RETRY_LIMIT:
                    raise RuntimeError(
                        f"{name} transient network failure: {exc}"
                    ) from exc
                delay = min(AI_RETRY_CAP_SECONDS, 0.8 * (2 ** attempt))
                await asyncio.sleep(delay + random.uniform(0.05, 0.35))
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise RuntimeError(f"{name} invalid response: {exc}") from exc

    return None


async def analyze_with_fallback(
    snapshot: MarketSnapshot,
) -> dict[str, Any] | None:
    """Bounded, non-blocking verification path used by pre-cycle review tasks."""
    key = _snapshot_key(snapshot)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    prompt = _prompt(snapshot)
    last_error: Exception | None = None

    for name in _providers():
        if _cooldown_active(name):
            continue
        if not _cfg(name):
            continue
        try:
            result = await _call_provider(name, snapshot, prompt)
            if result is not None:
                _cache_put(key, result)
                return result
        except Exception as exc:
            last_error = exc
            # A failed provider is isolated. Never sleep/block the scheduler here.
            continue

    if last_error:
        raise RuntimeError(f"AI verification unavailable: {last_error}")
    return None

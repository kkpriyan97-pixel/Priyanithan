import asyncio
import json
import logging
import os
import time
from typing import Any

from olymptrade_ws import OlympTradeClient
from olymptrade_ws.olympconfig import parameters

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nexora_ai")

STATE: dict[str, Any] = {
    "status": "starting",
    "asset": None,
    "asset_data": None,
    "asset_list": [],
    "last_price": None,
    "last_tick_ts": None,
    "candles": 0,
    "read_only": True,
}


async def on_tick(message: dict) -> None:
    for tick in message.get("d", []) or []:
        if not isinstance(tick, dict):
            continue
        pair = tick.get("p") or tick.get("pair")
        if pair == STATE.get("asset"):
            STATE["last_price"] = tick.get("q")
            STATE["last_tick_ts"] = tick.get("t")
            log.info(
                "ASSET_TICK asset=%s price=%s ts=%s",
                pair,
                tick.get("q"),
                tick.get("t"),
            )


def pair_name(item: dict) -> str:
    return str(
        item.get("pair")
        or item.get("p")
        or item.get("symbol")
        or item.get("instrument")
        or item.get("id")
        or ""
    )


def display_name(item: dict, fallback: str = "") -> str:
    """Read the account's own display name; never synthesize or rename it."""
    for key in ("title", "name", "display_name", "displayName"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback.strip() if isinstance(fallback, str) else ""


def signal_asset_label(item: dict) -> str:
    """Return the required user-facing label from live account metadata.

    The left side is always the account-provided display name. The pair is
    appended only in parentheses as the internal/API identifier. No hardcoded
    asset-name mapping is used.
    """
    name = str(item.get("display_name") or item.get("title") or "").strip()
    pair = str(item.get("pair") or "").strip()
    if not name or not pair:
        return ""
    return f"{name} ({pair})"


def event_records(client: OlympTradeClient, event_id: int) -> list[dict]:
    records: list[dict] = []
    try:
        cached = client.get_cached_events(event_id)
    except Exception:
        cached = []
    for message in cached or []:
        if not isinstance(message, dict):
            continue
        data = message.get("d")
        if isinstance(data, list):
            records.extend(x for x in data if isinstance(x, dict))
    return records


def build_account_asset_list(client: OlympTradeClient, raw_assets: list[dict]) -> list[dict]:
    """
    Build the FT asset list from the authenticated account-visible feeds.

    API pair is kept only as an internal identifier. The user-facing name is
    taken from the authenticated instrument metadata and is never normalized,
    renamed, or converted from the API symbol.

    No static allowlist or screenshot-derived name list is used.
    """
    profitability: dict[str, int] = {}
    profitability_meta: dict[str, dict] = {}
    for item in event_records(client, 182):
        pair = pair_name(item)
        value = item.get("profitability")
        if pair and isinstance(value, (int, float)):
            profitability[pair] = int(value)
            profitability_meta[pair] = item

    instruments: dict[str, dict] = {}
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        pair = pair_name(item)
        if pair:
            instruments[pair] = item

    result: list[dict] = []
    for pair, profit in profitability.items():
        instrument = instruments.get(pair)
        if not instrument:
            continue

        # Prefer the authenticated instrument's original display name.
        # If that feed does not carry a title, use the same field from the
        # profitability record before falling back to the internal pair.
        meta = profitability_meta.get(pair, {})
        title = display_name(instrument)
        if not title:
            title = display_name(meta)
        if not title:
            log.warning("ASSET_DISPLAY_NAME_MISSING pair=%s", pair)
            continue

        result.append(
            {
                "pair": pair,
                "title": title,
                "display_name": title,
                "signal_asset_label": f"{title} ({pair})",
                "profitability": profit,
                "locked": instrument.get("locked") is True,
                "locked_trading": instrument.get("locked_trading") is True,
                "locked_reason": instrument.get("locked_reason") or "",
                "time_open": instrument.get("time_open"),
                "time_close": instrument.get("time_close"),
                "time_open_trading": instrument.get("time_open_trading"),
                "time_close_trading": instrument.get("time_close_trading"),
                "mode": "OTC" if "_OTC" in pair.upper() else "REAL",
                "instrument": instrument,
            }
        )
    return result


def is_currently_open(item: dict, now_ts: float | None = None) -> bool:
    if item.get("locked") or item.get("locked_trading"):
        return False

    # Explicit lock flags are the authoritative current-state fields.
    # Do not interpret time_open/time_close as current session state because
    # those fields may describe the surrounding/next session boundary.
    return True


def select_first_open_asset(account_assets: list[dict]) -> dict | None:
    open_real = [
        x for x in account_assets
        if x.get("mode") == "REAL" and is_currently_open(x)
    ]
    if not open_real:
        return None

    best_profit = max(int(x.get("profitability") or 0) for x in open_real)
    return next(x for x in open_real if int(x.get("profitability") or 0) == best_profit)


async def handle_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await reader.read(4096)
        body = json.dumps(
            {
                "service": "NEXORA-AI",
                "status": STATE.get("status"),
                "read_only": True,
                "asset": STATE.get("asset"),
                "asset_display_name": (STATE.get("asset_data") or {}).get("display_name"),
                "signal_asset_label": signal_asset_label(STATE.get("asset_data") or {}),
                "last_price": STATE.get("last_price"),
                "last_tick_ts": STATE.get("last_tick_ts"),
                "candles": STATE.get("candles"),
                "asset_list_count": len(STATE.get("asset_list") or []),
            }
        ).encode()
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Cache-Control: no-store\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        writer.write(response)
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def market_worker() -> None:
    while True:
        token = os.getenv("OLYMPTRADE_ACCESS_TOKEN")
        if not token:
            STATE["status"] = "waiting_for_token"
            log.error("OLYMPTRADE_ACCESS_TOKEN is not set in Render Environment Variables.")
            await asyncio.sleep(30)
            continue

        client = OlympTradeClient(access_token=token, log_raw_messages=False)
        client.register_callback(parameters.E_TICK_UPDATE, on_tick)

        try:
            STATE["status"] = "connecting"
            await client.start()
            STATE["status"] = "connected"
            log.info("NEXORA_AI_STARTED read_only=true")

            startup_subscriptions = [
                [220],
                [110, 700, 112, 140, 1038, 1037, 1039, 141, 22, 26, 111],
                [1054, 1076, 1301, 1097],
                [141, 241],
                [230, 231],
                [75],
                [1055],
                [2223, 2301, 55, 150, 152, 151, 126, 602, 601],
                [2076],
                [126],
            ]
            for sub in startup_subscriptions:
                await client.send_request(98, sub, requires_response=False)

            await asyncio.sleep(5)

            for message in client.get_cached_events(55):
                data = message.get("d") if isinstance(message, dict) else None
                if isinstance(data, list):
                    for account in data:
                        if isinstance(account, dict) and account.get("group") == "demo":
                            client.account_id = account.get("account_id")
                            client.account_group = "demo"
                            break
                if client.account_id:
                    break

            log.info(
                "AUTH_SESSION_READY account_id=%s account_group=%s",
                client.account_id,
                client.account_group,
            )

            raw_assets = await client.market.get_available_assets(client.account_id)
            otc_assets = await client.market.get_otc_assets(client.account_id)
            otc_pairs = sorted(
                {
                    pair_name(item)
                    for item in otc_assets
                    if isinstance(item, dict) and pair_name(item)
                }
            )
            log.info(
                "OTC_ASSET_ACCESS read_only=true count=%d pairs=%s",
                len(otc_pairs),
                ",".join(otc_pairs),
            )

            account_assets = build_account_asset_list(client, raw_assets)

            if len(account_assets) < 5:
                await asyncio.sleep(3)
                account_assets = build_account_asset_list(client, raw_assets)

            if not account_assets:
                raise RuntimeError(
                    "Authenticated Olymptrade FT asset list is empty: "
                    "no profitability/instrument intersection was received."
                )

            STATE["asset_list"] = [
                {k: v for k, v in item.items() if k != "instrument"}
                for item in account_assets
            ]

            open_assets = [x for x in account_assets if is_currently_open(x)]
            real_assets = [x for x in account_assets if x.get("mode") == "REAL"]
            otc_account_assets = [x for x in account_assets if x.get("mode") == "OTC"]

            log.info(
                "ACCOUNT_ASSET_LIST_READY total=%d open=%d closed=%d real=%d otc=%d",
                len(account_assets),
                len(open_assets),
                len(account_assets) - len(open_assets),
                len(real_assets),
                len(otc_account_assets),
            )
            log.info(
                "ACCOUNT_ASSET_LIST=%s",
                ",".join(
                    f"{x['signal_asset_label']}:{x['profitability']}:"
                    f"{'OPEN' if is_currently_open(x) else 'CLOSED'}"
                    for x in account_assets
                ),
            )

            selected = select_first_open_asset(account_assets)
            if not selected:
                raise RuntimeError(
                    "Authenticated account asset list is present, but no currently open REAL asset is available."
                )

            pair = selected["pair"]
            STATE["asset"] = pair
            STATE["asset_data"] = selected
            log.info(
                "FIRST_OLYMPTRADE_ASSET display_name=%s pair=%s profitability=%s mode=%s",
                selected["display_name"],
                pair,
                selected["profitability"],
                selected["mode"],
            )

            candles = await client.market.get_candles(pair, size=60, count=5)
            STATE["candles"] = len(candles or [])
            log.info(
                "ASSET_HISTORY_OK display_name=%s pair=%s candles=%d",
                selected["display_name"],
                pair,
                STATE["candles"],
            )

            await client.market.subscribe_ticks(pair)
            log.info(
                "ASSET_TICK_SUBSCRIBED display_name=%s pair=%s",
                selected["display_name"],
                pair,
            )
            STATE["status"] = "live_read_only"

            while True:
                await asyncio.sleep(30)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            STATE["status"] = "error"
            log.exception("MARKET_WORKER_ERROR: %s", exc)
            await asyncio.sleep(15)
        finally:
            try:
                await client.stop()
            except Exception:
                pass


async def main() -> None:
    port = int(os.getenv("PORT", "10000"))
    server = await asyncio.start_server(handle_http, "0.0.0.0", port)
    log.info("HTTP_HEALTH_SERVER_LISTENING port=%s", port)

    try:
        await market_worker()
    finally:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())

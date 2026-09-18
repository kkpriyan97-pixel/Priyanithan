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


def build_account_asset_list(client: OlympTradeClient) -> list[dict]:
    """
    Build the FT asset list from the authenticated account-visible feeds.

    e=182 is the full profitability list shown by the Olymptrade FT Assets panel.
    e=1054 supplies the authenticated instrument records, including lock/schedule
    state. The intersection is the source of truth. No static allowlist or
    screenshot-derived list is used by the bot.
    """
    profitability: dict[str, int] = {}
    for item in event_records(client, 182):
        pair = pair_name(item)
        value = item.get("profitability")
        if pair and isinstance(value, (int, float)):
            profitability[pair] = int(value)

    instruments: dict[str, dict] = {}
    for item in event_records(client, 1054):
        pair = pair_name(item)
        if pair:
            instruments[pair] = item

    result: list[dict] = []
    for pair, profit in profitability.items():
        instrument = instruments.get(pair)
        if not instrument:
            continue
        result.append(
            {
                "pair": pair,
                "title": instrument.get("title") or pair,
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

    now = time.time() if now_ts is None else now_ts
    open_ts = item.get("time_open_trading")
    close_ts = item.get("time_close_trading")

    # Schedule fields are authoritative when both are present and sensible.
    # Do not reject assets when the feed omits them or sends zero/null.
    if isinstance(open_ts, (int, float)) and isinstance(close_ts, (int, float)):
        if open_ts > 0 and close_ts > 0:
            if now < open_ts or now >= close_ts:
                return False

    return True


def select_first_open_asset(account_assets: list[dict]) -> dict | None:
    open_real = [
        x for x in account_assets
        if x.get("mode") == "REAL" and is_currently_open(x)
    ]
    if not open_real:
        return None

    # Do not use arbitrary raw instrument order. Prefer the highest current
    # account-visible profitability, then preserve the authenticated list order.
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

            # Allow the authenticated account/instrument/profitability pushes to
            # populate the event cache before constructing the list.
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

            # Keep the authenticated raw feed available for diagnostics, but do
            # not use its global order as the account asset list.
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

            account_assets = build_account_asset_list(client)

            # If the first cache snapshot arrived before the full feeds, wait
            # briefly and rebuild instead of falling back to a static/raw list.
            if len(account_assets) < 5:
                await asyncio.sleep(3)
                account_assets = build_account_asset_list(client)

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
                    f"{x['pair']}:{x['profitability']}:{'OPEN' if is_currently_open(x) else 'CLOSED'}"
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
                "FIRST_OLYMPTRADE_ASSET=%s profitability=%s mode=%s",
                pair,
                selected["profitability"],
                selected["mode"],
            )
            log.info("ASSET_DATA=%s", selected)

            candles = await client.market.get_candles(pair, size=60, count=5)
            STATE["candles"] = len(candles or [])
            log.info("ASSET_HISTORY_OK asset=%s candles=%d", pair, STATE["candles"])

            await client.market.subscribe_ticks(pair)
            log.info("ASSET_TICK_SUBSCRIBED asset=%s", pair)
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

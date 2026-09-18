import asyncio
import json
import logging
import os
from typing import Any

from olymptrade_ws import OlympTradeClient
from olymptrade_ws.olympconfig import parameters

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nexora_ai")

STATE: dict[str, Any] = {
    "status": "starting",
    "asset": None,
    "asset_data": None,
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

            # Start the read-only market/account subscriptions without blocking on
            # the optional account-info request. Asset/instrument pushes arrive asynchronously.
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

            # Allow the server's account/instrument pushes to populate the cache.
            await asyncio.sleep(5)

            # Prefer the demo account from the balance push for read-only discovery.
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

            assets = await client.market.get_available_assets(client.account_id)
            if not assets:
                await asyncio.sleep(3)
                assets = await client.market.get_available_assets(client.account_id)
            if not assets:
                raise RuntimeError("OlympTrade connected, but no asset/instrument records were returned.")

            # Prefer an unlocked, trade-enabled instrument; fall back to the first record.
            asset = next(
                (
                    item for item in assets
                    if isinstance(item, dict)
                    and not item.get("locked", True)
                    and not item.get("disabled", False)
                ),
                assets[0],
            )
            if not asset:
                raise RuntimeError("No authenticated OlympTrade asset was returned.")

            pair = (
                asset.get("pair")
                or asset.get("p")
                or asset.get("symbol")
                or asset.get("instrument")
            )
            if not pair:
                raise RuntimeError(f"Asset response has no pair identifier: {asset}")

            STATE["asset"] = str(pair)
            STATE["asset_data"] = asset
            log.info("FIRST_OLYMPTRADE_ASSET=%s", pair)
            log.info("ASSET_DATA=%s", asset)

            candles = await client.market.get_candles(str(pair), size=60, count=5)
            STATE["candles"] = len(candles or [])
            log.info("ASSET_HISTORY_OK asset=%s candles=%d", pair, STATE["candles"])

            await client.market.subscribe_ticks(str(pair))
            log.info("ASSET_TICK_SUBSCRIBED asset=%s", pair)
            STATE["status"] = "live_read_only"

            # Keep the authenticated read-only market connection alive.
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

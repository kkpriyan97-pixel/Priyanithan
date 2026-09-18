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

            # Read-only instrument/account pushes used by the authenticated
            # Olymptrade session. No order or trade endpoint is called here.
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

            assets = await client.market.get_available_assets(client.account_id)
            otc_assets = await client.market.get_otc_assets(client.account_id)
            otc_pairs = [
                str(
                    item.get("pair")
                    or item.get("p")
                    or item.get("symbol")
                    or item.get("instrument")
                    or item.get("id")
                    or ""
                )
                for item in otc_assets
                if isinstance(item, dict)
            ]
            otc_pairs = sorted({p for p in otc_pairs if p})
            log.info(
                "OTC_ASSET_ACCESS read_only=true count=%d pairs=%s",
                len(otc_pairs),
                ",".join(otc_pairs),
            )
            if not assets:
                await asyncio.sleep(3)
                assets = await client.market.get_available_assets(client.account_id)
            if not assets:
                raise RuntimeError("OlympTrade connected, but no asset/instrument records were returned.")

            def pair_name(item: dict) -> str:
                return str(
                    item.get("pair")
                    or item.get("p")
                    or item.get("symbol")
                    or item.get("instrument")
                    or item.get("id")
                    or ""
                )

            def is_open(item: dict) -> bool:
                # Missing lock flags are allowed; only an explicit True blocks an asset.
                return (
                    item.get("locked") is not True
                    and item.get("locked_trading") is not True
                    and item.get("disabled") is not True
                )

            def has_flex_metadata(item: dict) -> bool:
                multipliers = item.get("allowed_multiplicators")
                suggestions = item.get("multiplicator_suggestions")
                return (
                    (isinstance(multipliers, (list, tuple)) and bool(multipliers))
                    or (isinstance(suggestions, (list, tuple)) and bool(suggestions))
                )

            flex_assets = [
                item for item in assets
                if isinstance(item, dict)
                and has_flex_metadata(item)
                and is_open(item)
            ]

            flex_otc_assets = [
                item for item in flex_assets
                if "_OTC" in pair_name(item).upper()
            ]
            real_assets = [
                item for item in flex_assets
                if item not in otc_assets
            ]

            log.info(
                "FLEX_ASSET_SCAN total=%d flex_open=%d flex_real=%d flex_otc=%d",
                len(assets),
                len(flex_assets),
                len(real_assets),
                len(flex_otc_assets),
            )

            # OTC is discovered independently from Flex. Olymptrade documents
            # OTC under Fixed Time (FT), so OTC access stays read-only and is not
            # mixed into Flex selection.
            if otc_pairs:
                log.info("OTC_ASSET_LIST_READY count=%d", len(otc_pairs))
            else:
                log.warning("OTC_ASSET_LIST_EMPTY authenticated feed returned no OTC assets.")

            # Flex mode is kept separate from Fixed Time. If the authenticated
            # feed exposes no Flex-capable instrument, fail cleanly instead of
            # silently selecting a Fixed Time profitability-only record.
            if not flex_assets:
                raise RuntimeError("No open Flex-capable instrument was returned by the authenticated market feed.")

            # Prefer a real-market Flex instrument. OTC records are retained in
            # the diagnostic count but are not mixed into the Flex selection.
            asset = real_assets[0] if real_assets else flex_assets[0]

            pair = pair_name(asset)
            if not pair:
                raise RuntimeError(f"Asset response has no pair identifier: {asset}")

            STATE["asset"] = pair
            STATE["asset_data"] = asset
            log.info("FIRST_OLYMPTRADE_ASSET=%s", pair)
            log.info("ASSET_DATA=%s", asset)

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

import asyncio
import logging
import os

from olymptrade_ws import OlympTradeClient
from olymptrade_ws.olympconfig import parameters

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nexora_ai")

async def on_tick(message: dict) -> None:
    for tick in message.get("d", []) or []:
        if isinstance(tick, dict):
            pair = tick.get("p") or tick.get("pair")
            if pair == os.getenv("ACTIVE_ASSET"):
                log.info("ASSET_TICK asset=%s price=%s ts=%s", pair, tick.get("q"), tick.get("t"))

async def main() -> None:
    token = os.getenv("OLYMPTRADE_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("OLYMPTRADE_ACCESS_TOKEN is not set in Render Environment Variables.")

    client = OlympTradeClient(access_token=token, log_raw_messages=False)
    client.register_callback(parameters.E_TICK_UPDATE, on_tick)

    try:
        await client.start()
        log.info("NEXORA_AI_STARTED read_only=true")

        asset = await client.market.get_first_available_asset()
        if not asset:
            raise RuntimeError("No available OlympTrade asset was returned.")

        pair = asset.get("pair") if isinstance(asset, dict) else str(asset)
        os.environ["ACTIVE_ASSET"] = pair
        log.info("FIRST_OLYMPTRADE_ASSET=%s", pair)
        log.info("ASSET_DATA=%s", asset)

        candles = await client.market.get_candles(pair, size=60, count=5)
        log.info("ASSET_HISTORY_OK asset=%s candles=%d", pair, len(candles or []))

        await client.market.subscribe_ticks(pair)
        log.info("ASSET_TICK_SUBSCRIBED asset=%s", pair)

        await asyncio.sleep(30)
    finally:
        await client.stop()
        log.info("NEXORA_AI_STOPPED")

if __name__ == "__main__":
    asyncio.run(main())

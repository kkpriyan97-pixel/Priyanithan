#api/market.py
import logging
import time
from typing import TYPE_CHECKING, Dict, Any, Optional, List, Union
from datetime import datetime, timezone

if TYPE_CHECKING:
    from olymptrade_ws.core.client import OlympTradeClient

logger = logging.getLogger(__name__)

class MarketAPI:
    def __init__(self, client: 'OlympTradeClient'):
        self._client = client

    async def subscribe_ticks(self, pair: str) -> None:
        """Compatibility method; live tick events are already delivered by the session."""
        logger.info("Tick subscription skipped for %s; using broker live tick stream/candle polling.", pair)

    async def unsubscribe_ticks(self, pair: str) -> None:
        logger.info("Tick unsubscription skipped for %s.", pair)

    async def get_candles(self, pair: str, size: int, count: int, end_time: Optional[Union[datetime, int]] = None) -> Optional[List[Dict[str, Any]]]:
        """Request recent historical candles with an explicit range/count.

        The previous request sent only pair/size/to/solid. The broker was then returning
        very old cached candles for otherwise-live instruments. Include count and from so
        the server receives an explicit recent window ending at the current time.
        """
        if end_time is None:
            to_ts = int(time.time())
        elif isinstance(end_time, datetime):
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            to_ts = int(end_time.timestamp())
        else:
            to_ts = int(end_time)

        try:
            size = max(1, int(size))
            count = max(1, int(count))
        except (TypeError, ValueError):
            size, count = 60, 120

        from_ts = max(0, to_ts - (size * count))
        logger.info(
            "Requesting %s candles for %s (size: %ss) from=%s to=%s",
            count, pair, size,
            datetime.fromtimestamp(from_ts, tz=timezone.utc),
            datetime.fromtimestamp(to_ts, tz=timezone.utc),
        )

        data = [{
            "pair": pair,
            "size": size,
            "count": count,
            "from": from_ts,
            "to": to_ts,
            "solid": True,
        }]

        try:
            response = await self._client.send_request(10, data, requires_response=True)
            if not response:
                logger.warning("Empty candle response for %s.", pair)
                return None

            response_event = response.get("e")
            if response_event == 10:
                payload = response.get("d")
                if isinstance(payload, list) and payload and isinstance(payload[0], dict):
                    candles_data = payload[0].get("candles")
                    if isinstance(candles_data, list):
                        normalized = []
                        for candle in candles_data:
                            if not isinstance(candle, dict):
                                continue
                            item = dict(candle)
                            if "timestamp" not in item and "t" in item:
                                item["timestamp"] = item["t"]
                            if all(k in item for k in ("open", "low", "high", "close")):
                                normalized.append(item)
                        logger.info("Received %s historical candles for %s (e:10).", len(normalized), pair)
                        return normalized or None

            if response_event == 1003:
                candles_data = response.get("d")
                if isinstance(candles_data, list):
                    logger.info("Received %s candles for %s (legacy e:1003).", len(candles_data), pair)
                    return candles_data or None

            if response.get("err"):
                logger.warning("Candle request unavailable for %s: e:%s err=%s", pair, response_event, response.get("err"))
            else:
                logger.warning(
                    "Unexpected candle response for %s: e:%s keys:%s",
                    pair, response_event,
                    list(response.keys()) if isinstance(response, dict) else type(response),
                )
            return None
        except Exception as e:
            logger.warning("Failed to get candles for %s: %s", pair, e)
            return None

    async def get_profitability(self, account_id: int) -> Optional[List[Dict[str, Any]]]:
        """Requests current profitability for assets (Event 182)."""
        logger.info("Requesting asset profitability for account %s...", account_id)
        try:
            response = await self._client.send_request(182, [{"account_id": account_id}], requires_response=True)
            if response and response.get("e") == 182:
                profit_data = response.get("d")
                if isinstance(profit_data, list):
                    return profit_data
            logger.error("Unexpected profitability response: %s", response)
            return None
        except Exception as e:
            logger.error("Failed to get profitability: %s", e)
            return None

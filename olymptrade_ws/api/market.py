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
        """Keep compatibility with callers without sending the obsolete tick-subscription frames.

        The current OlympTrade WebSocket session already delivers tick events (e:1), while
        the old e:12/e:280 request format returns server-side ``invalid_request`` for the
        discovered instrument catalogue. Historical candles are requested independently by
        get_candles(), so sending these rejected subscription frames is unnecessary and only
        creates noise/errors in the connection log.
        """
        logger.info("Tick subscription skipped for %s; using broker live tick stream/candle polling.", pair)

    async def unsubscribe_ticks(self, pair: str) -> None:
        """No-op counterpart for the obsolete tick subscription protocol."""
        logger.info("Tick unsubscription skipped for %s.", pair)

    async def get_candles(self, pair: str, size: int, count: int, end_time: Optional[Union[datetime, int]] = None) -> Optional[List[Dict[str, Any]]]:
        """Requests historical candle data."""
        if end_time is None:
            to_ts = int(time.time())
        elif isinstance(end_time, datetime):
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            to_ts = int(end_time.timestamp())
        else:
            to_ts = int(end_time)

        logger.info(
            "Requesting %s candles for %s (size: %ss) ending around %s",
            count, pair, size, datetime.fromtimestamp(to_ts, tz=timezone.utc)
        )

        # Current observed OlympTrade response is event e:10 with candles nested under
        # d[0]["candles"]. The request shape below matches that observed response.
        data = [{"pair": pair, "size": size, "to": to_ts, "solid": True}]

        try:
            response = await self._client.send_request(10, data, requires_response=True)
            if not response:
                logger.error("Empty candle response for %s.", pair)
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

            # Backward compatibility with the legacy e:1003 response.
            if response_event == 1003:
                candles_data = response.get("d")
                if isinstance(candles_data, list):
                    logger.info("Received %s candles for %s (legacy e:1003).", len(candles_data), pair)
                    return candles_data or None

            logger.error(
                "Unexpected candle response for %s: e:%s keys:%s",
                pair, response_event,
                list(response.keys()) if isinstance(response, dict) else type(response),
            )
            return None
        except Exception as e:
            logger.error("Failed to get candles for %s: %s", pair, e)
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

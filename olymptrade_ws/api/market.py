13 and 281 are sent when unsubscribing
        try:
             # Event 13
            await self._client.send_request(13, [{"pair": pair}], requires_response=True)
            # Event 281
            await self._client.send_request(281, [{"pair": pair}], requires_response=True)
            logger.info(f"Successfully sent tick unsubscription requests for {pair}.")
        except Exception as e:
            logger.error(f"Failed to unsubscribe from ticks for {pair}: {e}")
            raise
            
    async def get_candles(
        self,
        pair: str,
        size: int,
        count: int,
        end_time: Optional[Union[datetime, int]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Request historical OHLC candles from OlympTrade.

        The observed OlympTrade response is event ``10`` with payload:
        ``{"d": [{"pair": ..., "tf": 60, "candles": [...] }], "e": 10}``.
        Each candle uses ``t/open/low/high/close`` fields.

        ``app.py`` already normalizes these field names, so this method returns
        the inner candle list directly. No order/trade method is called.
        """
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

        # Event 10 is both the candle request and the response event in the
        # observed API traffic. ``solid`` requests completed/closed candles.
        payload = [{
            "pair": pair,
            "size": int(size),
            "to": to_ts,
            "solid": True,
        }]

        try:
            response = await self._client.send_request(
                10, payload, requires_response=True
            )

            if not isinstance(response, dict):
                logger.error("Unexpected candle response type: %r", type(response))
                return None

            response_event = response.get("e")
            data = response.get("d")

            if response_event != 10 or not isinstance(data, list):
                logger.error(
                    "Unexpected candle response: event=%r data_type=%s",
                    response_event, type(data).__name__,
                )
                return None

            # Actual response shape: d=[{pair, tf, candles:[...]}]
            candles: List[Dict[str, Any]] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                if str(item.get("pair", "")).upper() != str(pair).upper():
                    continue
                rows = item.get("candles")
                if isinstance(rows, list):
                    candles.extend(x for x in rows if isinstance(x, dict))

            if not candles:
                # Be tolerant if the server returns the candle list directly.
                candles = [x for x in data if isinstance(x, dict) and "open" in x]

            if candles:
                candles.sort(key=lambda x: float(x.get("t", x.get("timestamp", 0))))
                logger.info("Received %s candles for %s", len(candles), pair)
                return candles[-int(count):]

            logger.warning("No candle rows returned for %s", pair)
            return None

        except Exception as e:
            logger.error("Failed to get candles for %s: %s", pair, e)
            return None

    async def get_profitability(self, account_id: int) -> Optional[List[Dict[str, Any]]]:
        """Requests current profitability for assets (Event 182)."""
        logger.info(f"Requesting asset profitability for account {account_id}...")
        event_code = 182
        data = [{"account_id": account_id}]
        try:
            response = await self._client.send_request(event_code, data, requires_response=True)
            if response and response.get("e") == event_code:
                profit_data = response.get("d")
                if isinstance(profit_data, list):
                    logger.info(f"Received profitability for {len(profit_data)} assets.")
                    return profit_data
                else:
                     logger.error(f"Unexpected data format in profitability response: {profit_data}")
                     return None
            else:
                logger.error(f"Did not receive expected profitability response (e:{event_code}). Got: {response}")
                return None
        except Exception as e:
            logger.error(f"Failed to get profitability: {e}")
            return None

    async def select_asset(self, pair: str, category: str = "digital") -> Optional[Dict[str, Any]]:
         """Selects an asset, potentially retrieving strike/payout info (Events 95, 80)."""
         logger.info(f"Selecting asset {pair} (category: {category})...")
         event_code_select = 95
         event_code_strikes = 80 # Often follows e:95 in logs
         data = [{"cat": category, "pair": pair}]
         try:
             # Send e:95 request
             response_select = await self._client.send_request(event_code_select, data, requires_response=True)
             if not (response_select and response_select.get("e") == event_code_select):
                 logger.error(f"Failed to get confirmation for asset selection (e:{event_code_select}).")
                 # Decide if we should proceed to wait for strikes anyway
             
             logger.info(f"Asset {pair} selected. Waiting for strike/payout info (e:{event_code_strikes})...")
             # Event 80 seems to be pushed after 95, not a direct response.
             # We need a way to wait for a specific *unsolicited* event.
             # Option 1: Register a temporary callback for e:80 with a filter for the pair.
             # Option 2: Have a general e:80 callback update internal state, then retrieve it.
             
             # Using Option 1 (temporary callback) for demonstration:
             future = asyncio.get_running_loop().create_future()

             async def temp_strike_callback(message: Dict[str, Any]):
                 strike_data_list = message.get("d", [])
                 if isinstance(strike_data_list, list):
                     for item in strike_data_list:
                         # Check if this strike data is for the requested pair
                         if isinstance(item, dict) and item.get("p") == pair:
                              if not future.done():
                                   future.set_result(item) # Return the specific strike data for the pair
                              break # Found our pair

             self._client.register_callback(event_code_strikes, temp_strike_callback)
             
             try:
                 # Wait for the callback to set the future's result
                 strike_info = await asyncio.wait_for(future, timeout=settings.DEFAULT_RESPONSE_TIMEOUT)
                 logger.info(f"Received strike info for {pair}: {strike_info}")
                 return strike_info
             except asyncio.TimeoutError:
                  logger.error(f"Timeout waiting for strike info (e:{event_code_strikes}) for {pair}.")
                  return None
             finally:
                  # Always unregister the temporary callback
                  self._client.unregister_callback(event_code_strikes, temp_strike_callback)

         except Exception as e:
             logger.error(f"Failed during asset selection/strike retrieval for {pair}: {e}")
             return None

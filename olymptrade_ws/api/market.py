169
170
171
172
173
174
175
176
177
178
179
180
181
182
183
184
185
186
187
188
189
190
191
192
193
194
195
196
197
198
199
200
201
202
# api/market.py
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

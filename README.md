# Candice AI v8.5 — FLEX Manual Signal Bot

Clean read-only rebuild of the Priyanithan Telegram signal bot.

## Current full-bot design
- Live OlympTrade market data through the existing WebSocket market adapter.
- **FLEX / Fixed-Time only. Forex mode is OFF.**
- Telegram alerts only; **no broker order placement**.
- Martingale OFF.
- Every **1-minute closed candle** is analyzed on a continuous 1-minute scan cadence.
- Deterministic technical analysis across 1m/3m/5m/10m/15m context.
- Groq is the primary AI gate with Cerebras as fallback; the AI decision must agree with the technical direction and meet the confidence gate before an alert is sent.
- Adaptive expiries are **1 / 2 / 3 / 5 / 10 / 15 minutes**. Candice selects duration from current market strength and higher-timeframe alignment; the AI gate can refine the candidate duration.
- Server-side signal countdown is tied to the closed entry-candle boundary and selected expiry, independent of message refresh timing.
- Expiry is verified against a fully closed 1-minute boundary candle before WIN/LOSS is recorded.
- Duplicate prevention is based on user + asset + closed entry-candle timestamp.
- Daily-loss stop and 3-consecutive-loss stop are enforced.
- A loss starts a short recovery pause; no martingale is used.
- Market-outcome WIN/LOSS/DRAW tracking is stored in bounded local memory.
- Telegram uses text-only signal/result messages; no image or animation sending.

## Adaptive signal lifecycle
1. Refresh live FLEX assets.
2. Analyze fresh closed 1-minute data.
3. Build 3m/5m/10m/15m higher-timeframe context.
4. Apply deterministic technical gates.
5. Select a candidate duration from market state: 1, 2, 3, 5, 10, or 15 minutes.
6. Run the AI decision gate and allow only an approved allowed duration.
7. Verify the latest closed 1-minute entry candle.
8. Prevent duplicate entry-candle signals.
9. Send the manual signal with direction, confidence, expiry, entry and countdown.
10. Wait for the server-side expiry boundary.
11. Verify the expiry candle is fully closed.
12. Record and send WIN / LOSS / DRAW.
13. Apply risk protection when required.

## Safety boundary
This project deliberately contains no broker order placement from the application workflow. The recorded WIN/LOSS is a **market outcome** calculated from the signal entry reference and the verified expiry candle close; it is not a claim about broker account P/L.

## Required Render secrets
Set `TELEGRAM_BOT_TOKEN`, `ACCESS_CODE`, `OLYMPTRADE_ACCESS_TOKEN`, and the AI provider keys in Render environment variables. Never paste secret values into chat or logs.

Recommended operation settings:
- `SCAN_INTERVAL_SECONDS=60`
- `AI_MIN_CONFIDENCE=72`
- `DAILY_MAX_LOSSES=5`
- `MAX_CONSECUTIVE_LOSSES=3`
- `AI_TIMEOUT_SECONDS=18`
- `GROQ_MODEL=openai/gpt-oss-120b`
- `CEREBRAS_MODEL=gpt-oss-120b`

## Start command
`PYTHONPATH=. python app.py`

## Important limitation
GitHub CI verifies code compilation, safety contracts, deterministic candle/expiry behavior, and broker-to-engine read-only integration with a deterministic market stub. It does **not** prove live broker connectivity, live Telegram delivery, live AI-provider availability, or trading profitability. Those require the configured Render service and real-time external systems.

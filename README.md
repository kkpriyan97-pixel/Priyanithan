# Candice AI v8.2 — FLEX Manual Signal Bot

Clean read-only rebuild of the Priyanithan Telegram signal bot.

## Current full-bot design
- Live OlympTrade market data through the existing WebSocket market adapter.
- **FLEX / Fixed-Time only. Forex mode is OFF.**
- Telegram alerts only; **no broker order placement**.
- Martingale OFF.
- Every **1-minute closed candle** is analyzed on a continuous 1-minute scan cadence.
- Deterministic technical analysis across 1m/3m/5m/10m/15m context.
- Optional OpenRouter AI gate must agree with the technical direction and meet the confidence gate before an alert is sent.
- Allowed expiries are **2 / 3 / 5 / 15 minutes**. There is no 10-minute expiry.
- Server-side signal countdown uses entry timestamp + selected expiry and is independent of message refresh timing.
- Expiry is verified against a fully closed 1-minute boundary candle before WIN/LOSS is recorded.
- Duplicate prevention is based on user + asset + closed entry-candle timestamp.
- Daily-loss stop and 3-consecutive-loss stop are enforced.
- A loss starts a short recovery pause; no martingale is used.
- Market-outcome WIN/LOSS/DRAW tracking is stored in bounded local memory.
- Telegram uses text-only signal/result cards; no image or animation sending.

## Signal lifecycle
1. Refresh live FLEX assets.
2. Analyze fresh closed 1-minute data.
3. Build higher-timeframe context.
4. Apply deterministic technical gates.
5. Run the optional AI decision gate.
6. Verify the latest closed 1-minute entry candle.
7. Prevent duplicate entry-candle signals.
8. Send the manual signal with direction, confidence, expiry, entry and countdown.
9. Wait for the server-side expiry boundary.
10. Verify the expiry candle is fully closed.
11. Record and send WIN / LOSS / DRAW.
12. Apply risk protection when required.

## Safety boundary
This project deliberately contains no broker order placement from the application workflow. The recorded WIN/LOSS is a **market outcome** calculated from the signal entry reference and the verified expiry candle close; it is not a claim about broker account P/L.

## Required Render secrets
Set `TELEGRAM_BOT_TOKEN`, `ACCESS_CODE`, `OLYMPTRADE_ACCESS_TOKEN`, and `OPENROUTER_API_KEY` in Render environment variables. Never paste secret values into chat or logs.

Recommended operation settings:
- `SCAN_INTERVAL_SECONDS=60`
- `AI_MIN_CONFIDENCE=72`
- `DAILY_MAX_LOSSES=5`
- `MAX_CONSECUTIVE_LOSSES=3`
- `AI_TIMEOUT_SECONDS=18`

## Start command
`PYTHONPATH=. python app.py`

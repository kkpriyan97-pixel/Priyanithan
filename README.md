# Candice AI v8 — FLEX Manual Signal Bot

Clean rebuild of the Priyanithan Telegram signal bot.

## Design
- Live OlympTrade market data through the existing WebSocket market adapter.
- **FLEX / Fixed-Time only. Forex mode is OFF.**
- Telegram alerts only; **no broker order placement**.
- Martingale OFF.
- Fresh candles + deterministic technical analysis + optional OpenRouter AI gate.
- AI must agree with technical direction and meet the confidence gate before an alert is sent.
- 2/3/5/10/15 minute expiries.
- 5-minute scan cadence.
- 24/7 read-only research; signal generation only in signal sessions.
- Duplicate prevention, daily-loss stop and 3-consecutive-loss stop.
- Market-outcome WIN/LOSS/DRAW tracking with bounded evidence memory.
- Compact mobile Telegram animation cards.

## Session model
UTC runs in repeating 3-hour blocks: first 2 hours are SIGNAL, final hour is RESEARCH ONLY. Research continues throughout the day; during signal hours only the user's selected FLEX asset can produce an alert.

## Safety boundary
This project deliberately contains no call to a broker trade/order API from the application workflow. A recorded WIN/LOSS is the result of comparing the signal entry reference with the observed expiry market price; it is not a claim about an actual broker account trade or profit.

## Required Render secrets
Set `TELEGRAM_BOT_TOKEN`, `ACCESS_CODE`, `OLYMPTRADE_ACCESS_TOKEN`, and (for AI-approved signals) `OPENROUTER_API_KEY` in Render environment variables. Never paste secret values into chat or logs.

Start command: `PYTHONPATH=. python app.py`

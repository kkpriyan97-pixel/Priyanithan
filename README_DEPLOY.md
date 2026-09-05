# Priyanithan AI OlympTrade Live Signal Bot — v2

## What this build does
- Reads market data from the supplied OlympTrade WebSocket library.
- Analyzes candles, price action, market structure, EMA, MACD, RSI, Bollinger Bands, Stochastic and ADX.
- ADX < 20 => NO SIGNAL.
- Does not force a signal on every scan.
- Uses AI validation when an AI provider key is configured.
- Sends Telegram signals only after local filters + AI approval.
- Auto-trade is permanently OFF in this app; no order-placement method is called.
- Martingale is OFF.

## Render settings
Build Command:
`pip install -r requirements.txt`

Start Command:
`python app.py`

Required Render Environment Variables:
- TELEGRAM_BOT_TOKEN
- ACCESS_CODE
- OLYMPTRADE_ACCESS_TOKEN

Recommended AI:
- OPENROUTER_API_KEY
- OPENROUTER_MODEL=z-ai/glm-5.2:free

Optional:
- AIRFORCE_API_KEY
- AIRFORCE_MODEL=gpt-oss-120b
- OLYMP_PAIRS=EURUSD,GBPUSD
- OT_EURUSD_PAIR=EURUSD
- OT_GBPUSD_PAIR=GBPUSD
- SCAN_INTERVAL_SECONDS=300
- AI_MIN_CONFIDENCE=89

Never paste secrets into chat or commit them to GitHub. Put them in Render Environment Variables.

## Important verification
The supplied OlympTrade library's candle protocol is not independently verified in this package. If the broker returns a different candle payload, the bot refuses to generate a signal rather than inventing data.

The live chart renderer is included for local chart generation from broker candles. It is not a claim that the bot can capture the private OlympTrade UI itself.

Manual trade monitoring is read-only. Final WIN/LOSS should only be reported from a verified broker settlement/result event; the bot never infers a result from AI alone.

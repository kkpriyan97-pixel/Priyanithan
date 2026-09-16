# Candice AI

Clean rebuild from an empty repository.

## Brain workflow

Candice watches all available tradeable assets from the live Olymp Trade feed. It continuously evaluates live price and live 1-minute candles, the previous 60 minutes of behaviour, 15-minute trend, candle patterns, Candice strategies, expiry choices, and historical outcomes. The supplied final workflow requires one strongest valid setup per 5-minute decision cycle, while never forcing a bad signal merely to fill a slot.

Signal timing uses a continuous live decision window from 40 seconds before the target 5-minute boundary until the boundary. Entry is read from the live price at send time rather than a frozen snapshot.

After a signal, the engine tracks the same asset through expiry and records WIN, LOSS, or TIE for learning. Duplicate signals for the same cycle/asset are prevented.

## Safety mode

This project is read-only/manual-only. It contains no broker order-placement method, no martingale engine, and no automatic trade execution.

## Runtime

- Python 3
- Flask status endpoint
- WebSocket live market feed
- Telegram signal/result delivery
- UAE/Dubai timezone (`Asia/Dubai`)

## Required environment

- `OLYMPTRADE_ACCESS_TOKEN`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- optional `OLYMPTRADE_ASSETS`
- optional `CANDICE_LEARNING_FILE`

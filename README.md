# Candice AI

Clean rebuild from an empty repository.

## Brain workflow

Candice watches all available tradeable assets from the live Olymp Trade feed. It continuously evaluates live price and live 1-minute candles, the previous 60 minutes of behaviour, a 15-candle 1-minute trend view, candle patterns, Candice strategies, expiry choices, and historical outcomes. The workflow requires one strongest valid setup per 5-minute decision cycle, while never forcing a bad signal merely to fill a slot.

Signal timing uses a continuous live decision window from 40 seconds before the target 5-minute boundary until the boundary. Entry is read from the live price at send time rather than a frozen snapshot.

After a signal, the engine tracks the same asset through expiry and records WIN, LOSS, or TIE for learning. Duplicate signals for the same cycle/asset are prevented.

## Market discovery

Asset discovery is dynamic. The WebSocket client accepts wildcard callbacks for metadata-bearing responses, the discovery layer listens across known market/instrument event families, and new assets are subscribed asynchronously so the WebSocket dispatcher cannot deadlock waiting for candle history. No single asset is inserted as a fake fallback when discovery returns nothing. `OLYMPTRADE_ASSETS` can still be used to explicitly configure a comma-separated list.

## Safety mode

This project is read-only/manual-only. It contains no broker order-placement method, no martingale engine, and no automatic trade execution.

## Runtime

- Python 3
- Flask live dashboard and `/status` JSON endpoint
- WebSocket live market feed
- Dynamic multi-asset discovery and 1-minute candles
- Telegram signal/result delivery
- Telegram webhook on Render (polling fallback for local/non-Render use)
- UAE/Dubai timezone (`Asia/Dubai`)

## Required environment

- `OLYMPTRADE_ACCESS_TOKEN`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- optional `OLYMPTRADE_ASSETS`
- optional `CANDICE_LEARNING_FILE`
- optional `TELEGRAM_WEBHOOK_URL` (otherwise Render's external URL is used automatically)

# Deborah's Crypto Analyst — Telegram companion bot

A separate companion bot prototype. It does not modify, connect to, or replace your existing Telegram bot. It does not place trades.

## Features
- `/analyze ZECUSDT` (also supports symbols such as ETHUSDT, BTCUSDT, SOLUSDT, XRPUSDT) retrieves public Binance SPOT candles and reports a basic 4H/1H/15M trend snapshot, RSI, EMA, ATR, recent range and conditional breakout checks.
- `/log SYMBOL LONG|SHORT ENTRY STOP TARGET SETUP` creates an OPEN journal record and calculates illustrative sizing from configured equity/risk.
- `/close ID EXIT_PRICE [FEES_TOTAL]` closes a journal record using the exit price and fees you enter.
- `/journal` shows recent entries; `/stats` summarizes recorded closed trades; `/settings` shows configuration.
- SQLite journal persists across restarts if the database file is stored on persistent disk/volume.

## Security
1. Create the bot through Telegram's official `@BotFather`.
2. Store its API token as the private `TELEGRAM_BOT_TOKEN` environment variable on your host.
3. Never commit the token to source control, paste it into a public chat, or place it in the ZIP.
4. If a token is exposed, revoke it through BotFather and issue a new one.

## Run locally
Requires Python 3.10+ and network access.

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN='YOUR_PRIVATE_TOKEN'
export ACCOUNT_EQUITY='2394'
export RISK_PERCENT='1'
export LEVERAGE='10'
python bot.py
```
PowerShell environment syntax: `$env:TELEGRAM_BOT_TOKEN="YOUR_PRIVATE_TOKEN"`.

## Hosting
Deploy as a continuously running Python worker or Docker service. Add the token as a secret/environment variable. Mount a persistent volume at `/data` for SQLite data (the included Dockerfile sets `DB_PATH=/data/trades.db`). Do not use an ephemeral filesystem if you want the journal to survive redeploys. Keep the bot private initially and test with small sample records.

## Example commands
- `/analyze ZECUSDT`
- `/log ZECUSDT LONG 1350 1328 1395 breakout`
- `/close 1 1392.5 2.10`
- `/journal`
- `/stats`

## Important limitations
- Market data is public Binance SPOT data, not Margex perpetual-futures data. Do not assume prices match.
- The indicator/trigger rules are simple heuristics, not a validated profitable strategy or a backtest.
- Binance's latest candle may still be forming; confirm completed candles manually.
- Sizing calculations assume a simplified linear instrument and ignore contract-specific rules, fees/slippage until manually entered, funding, and liquidation mechanics. Verify all sizing on the exchange.
- The journal is manual. It does not detect fills or automatically reconcile account activity. `/close` P&L is an estimate based on logged quantity and user-entered fees.
- Configure `ACCOUNT_EQUITY` again when your equity changes. This prototype is not financial advice.

"""Run ETH/ZEC analysis once and send reports to Telegram (no polling)."""
import os
import sys
import time
from urllib.parse import urlsplit

import requests
import bot
from coinbase_data import candles as coinbase_candles

# Replace the data provider only for scheduled runs.
bot.candles = coinbase_candles
analyze = bot.analyze


def send_message(token: str, chat_id: str, message: str) -> None:
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = []
    current = ""
    for line in message.splitlines(keepends=True):
        if len(current) + len(line) > 3900 and current:
            chunks.append(current)
            current = ""
        while len(line) > 3900:
            chunks.append(line[:3900])
            line = line[3900:]
        current += line
    if current:
        chunks.append(current)

    for chunk in chunks:
        response = requests.post(endpoint, data={"chat_id": chat_id, "text": chunk}, timeout=20)
        response.raise_for_status()
        if not response.json().get("ok"):
            raise RuntimeError("Telegram rejected the message")


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secret", file=sys.stderr)
        return 1

    symbols = [s.strip().upper() for s in os.getenv("ANALYSIS_SYMBOLS", "ETHUSDT,ZECUSDT").split(",") if s.strip()]
    if not symbols:
        print("No symbols configured", file=sys.stderr)
        return 1

    failures = []
    for symbol in symbols:
        if not symbol.isalnum() or len(symbol) > 20:
            failures.append(symbol)
            print(f"Invalid symbol: {symbol}", file=sys.stderr)
            continue
        try:
            report, _ = analyze(symbol)
            send_message(token, chat_id, report.replace("**", "").replace("Public Binance SPOT candles", "Coinbase Exchange USD spot candles (USDT proxy)").replace("Binance SPOT data is not Margex perpetual-futures data", "Coinbase USD SPOT data is not USDT perpetual-futures data"))
            print(f"Sent {symbol} analysis to Telegram")
        except requests.exceptions.HTTPError as exc:
            failures.append(symbol)
            response = exc.response
            status = response.status_code if response is not None else "unknown"
            host = urlsplit(response.url).hostname if response is not None else "unknown"
            # Print host and status ONLY: full Telegram URLs can contain the bot token.
            print(f"{symbol}: HTTP error from {host}, status {status}", file=sys.stderr)
        except Exception as exc:
            failures.append(symbol)
            print(f"{symbol}: {type(exc).__name__}: {str(exc)[:160] if not isinstance(exc, requests.exceptions.RequestException) else 'network request failed'}", file=sys.stderr)
        time.sleep(1)

    if failures:
        print("Symbols failed: " + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

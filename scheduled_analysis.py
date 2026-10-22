"""Daily ETH/ZEC reports plus selective breakout alerts (no polling or orders).

Upload as scheduled_analysis.py alongside bot.py and coinbase_data.py.
"""
import os
import sys
import time
from urllib.parse import urlsplit

import requests
import bot
from coinbase_data import candles as coinbase_candles

bot.candles = coinbase_candles


def send_message(token, chat_id, message):
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    current = ""
    chunks = []
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
            raise RuntimeError("Telegram rejected message")


def classify(frames):
    """Uses completed Coinbase candles from coinbase_data.py."""
    c = frames[2]
    bulls = sum(x["trend"] == "BULLISH" for x in frames)
    bears = sum(x["trend"] == "BEARISH" for x in frames)
    long_breakout = c["close"] > c["swing_high"] and c["vol_ratio"] >= 1.1 and c["rsi"] < 75
    short_breakout = c["close"] < c["swing_low"] and c["vol_ratio"] >= 1.1 and c["rsi"] > 25
    if bulls >= 2 and long_breakout:
        return "TRIGGER LONG"
    if bears >= 2 and short_breakout:
        return "TRIGGER SHORT"
    if bulls >= 2:
        return "WATCH LONG"
    if bears >= 2:
        return "WATCH SHORT"
    return "WAIT"


def opportunity_message(symbol, frames, status):
    c = frames[2]
    price = bot.fmt_price(c["close"])
    stop = c["swing_low"] - 0.25 * c["atr"] if status == "TRIGGER LONG" else c["swing_high"] + 0.25 * c["atr"]
    distance = abs(c["close"] - stop)
    target = c["close"] + 2 * distance if status == "TRIGGER LONG" else c["close"] - 2 * distance
    return (
        f"🚨 POTENTIAL {status} — {symbol}\n"
        "Based on the most recent COMPLETED Coinbase 15m candle.\n"
        f"15m close: {price} USD (USD spot proxy for USDT)\n"
        f"Breakout level: {bot.fmt_price(c['swing_high'] if status == 'TRIGGER LONG' else c['swing_low'])}\n"
        f"Volume ratio: {c['vol_ratio']:.2f}x | RSI: {c['rsi']:.1f}\n"
        f"Illustrative structural stop: {bot.fmt_price(stop)}\n"
        f"Illustrative 2R target: {bot.fmt_price(target)}\n"
        "Not an entry order. Verify live prices, spreads, liquidity, fees, and liquidation on your execution venue."
    )


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID", file=sys.stderr)
        return 1

    symbols = [s.strip().upper() for s in os.getenv("ANALYSIS_SYMBOLS", "ETHUSDT,ZECUSDT").split(",") if s.strip()]
    failures = []
    for symbol in symbols:
        if not symbol.isalnum() or len(symbol) > 20:
            failures.append(symbol)
            print(f"Invalid symbol: {symbol}", file=sys.stderr)
            continue
        try:
            report, frames = bot.analyze(symbol)
            status = classify(frames)
            report = report.replace("**", "")
            report = report.replace("Public Binance SPOT candles", "Coinbase Exchange USD spot candles (USDT proxy)")
            report = report.replace("Binance SPOT data is not Margex perpetual-futures data", "Coinbase USD SPOT data is not USDT perpetual-futures data")
            report += f"\n\nOpportunity alert status (completed candles): {status}"
            send_message(token, chat_id, report)
            print(f"Sent {symbol} daily report; status={status}")
            if status.startswith("TRIGGER"):
                send_message(token, chat_id, opportunity_message(symbol, frames, status))
                print(f"Sent {symbol} opportunity alert")
        except requests.exceptions.HTTPError as exc:
            failures.append(symbol)
            response = exc.response
            status = response.status_code if response is not None else "unknown"
            host = urlsplit(response.url).hostname if response is not None else "unknown"
            print(f"{symbol}: HTTP error from {host}, status {status}", file=sys.stderr)
        except Exception as exc:
            failures.append(symbol)
            print(f"{symbol}: {type(exc).__name__}", file=sys.stderr)
        time.sleep(1)

    if failures:
        print("Symbols failed: " + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


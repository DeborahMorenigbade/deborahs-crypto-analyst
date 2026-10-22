"""Run one scheduled market snapshot and deliver it to a private Telegram chat.

This is intentionally separate from bot.py's long-running polling loop.
"""
import os
import sys
import time
import requests

from bot import analyze


def send_message(token: str, chat_id: str, text: str) -> None:
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram has a 4096-character message limit; split at newline boundaries.
    chunks = []
    current = ""
    for line in text.splitlines(keepends=True):
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
        response = requests.post(
            endpoint,
            data={"chat_id": chat_id, "text": chunk},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError("Telegram did not accept the message")


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID GitHub Actions secret.", file=sys.stderr)
        return 1

    symbols = [s.strip().upper() for s in os.getenv("ANALYSIS_SYMBOLS", "ETHUSDT,ZECUSDT").split(",") if s.strip()]
    if not symbols:
        print("No symbols configured.", file=sys.stderr)
        return 1

    failures = []
    for symbol in symbols:
        if not symbol.isalnum() or len(symbol) > 20:
            failures.append(symbol)
            print(f"Invalid symbol: {symbol}", file=sys.stderr)
            continue
        try:
            report, _ = analyze(symbol)
            # The report uses ** for emphasis but we send plain text, without Markdown parse_mode.
            report = report.replace("**", "")
            send_message(token, chat_id, report)
            print(f"Sent {symbol} analysis to Telegram.")
        except Exception as exc:
            import traceback
            traceback.print_exc()
            # Avoid logging raw HTTP request URLs containing the bot token.
            failures.append(symbol)
            print(f"Failed to analyze or deliver {symbol}: {type(exc).__name__}", file=sys.stderr)
        time.sleep(1)
    if failures:
        print("Some symbols failed: " + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

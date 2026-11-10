"""Standalone 15-minute opportunity alerts. Does not alter daily analysis or place orders."""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import bot
from coinbase_data import candles as coinbase_candles
from forex_analysis import PAIRS, get_candles, frame, fmt
from scheduled_analysis import send_message

STATE = Path(os.getenv('ALERT_STATE_FILE', 'alert_state.json'))
MAX_AGE = 35 * 60


def load_state():
    try:
        data = json.loads(STATE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(data):
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(data, sort_keys=True))
    temp.replace(STATE)


def fresh(timestamp):
    age = datetime.now(timezone.utc).timestamp() - timestamp
    return -60 <= age <= MAX_AGE


def trend_aligned(frames, side):
    direction = 'BULLISH' if side == 'LONG' else 'BEARISH'
    return all(f['trend'] == direction for f in frames)


def candidate(side, close, high, low, rsi, atr):
    if side == 'LONG':
        if not (close > high and 50 <= rsi < 75):
            return None
        stop = low - 0.25 * atr
        if not stop < close:
            return None
        return stop, close + 2 * (close - stop), high
    if not (close < low and 25 < rsi <= 50):
        return None
    stop = high + 0.25 * atr
    if not stop > close:
        return None
    return stop, close - 2 * (stop - close), low


def crypto_signal(symbol):
    bot.candles = coinbase_candles
    frames = [bot.frame_analysis(symbol, interval, label) for interval, label in bot.TIMEFRAMES]
    last = coinbase_candles(symbol, '15m', 65)[-1]
    candle_ts = int(last[0]) / 1000
    if not fresh(candle_ts + 900):
        print(f'{symbol}: stale 15m candle, skipping')
        return None
    c = frames[-1]
    for side in ('LONG', 'SHORT'):
        if not trend_aligned(frames, side) or c['vol_ratio'] < 1.1:
            continue
        result = candidate(side, c['close'], c['swing_high'], c['swing_low'], c['rsi'], c['atr'])
        if result:
            stop, target, level = result
            f = bot.fmt_price
            message = (f'🚨 POTENTIAL {side} SETUP — {symbol}\n'
                       f'Completed Coinbase 15M candle: {datetime.fromtimestamp(candle_ts, timezone.utc):%Y-%m-%d %H:%M UTC}\n'
                       '4H and 1H trend aligned with 15M.\n'
                       f'Close: {f(c["close"])} | breakout level: {f(level)}\n'
                       f'RSI: {c["rsi"]:.1f} | volume: {c["vol_ratio"]:.2f}× 20-bar average\n'
                       f'Illustrative stop: {f(stop)} | 2R target: {f(target)}\n'
                       'Coinbase USD spot is a proxy, not your USDT futures venue. Verify spread, fees, funding, liquidation and live levels. Not an order.')
            return f'{symbol}:{int(candle_ts)}:{side}', message
    return None


def forex_signal(pair):
    frames = []
    for idx, (interval, label) in enumerate((('4h', '4H'), ('1h', '1H'), ('15min', '15M'))):
        if idx:
            time.sleep(9)  # Twelve Data Basic per-minute rate pacing
        frames.append(frame(get_candles(pair, interval), label))
    c = frames[-1]
    candle_ts = c['time'].timestamp()
    if not fresh(candle_ts + 900):
        print(f'{pair}: stale 15m candle, skipping')
        return None
    for side in ('LONG', 'SHORT'):
        if not trend_aligned(frames, side):
            continue
        result = candidate(side, c['close'], c['high'], c['low'], c['rsi'], c['atr'])
        if result:
            stop, target, level = result
            message = (f'🚨 POTENTIAL {side} SETUP — {pair}\n'
                       f'Completed Twelve Data 15M candle: {c["time"]:%Y-%m-%d %H:%M UTC}\n'
                       '4H and 1H trend aligned with 15M.\n'
                       f'Close: {fmt(pair, c["close"])} | breakout level: {fmt(pair, level)}\n'
                       f'RSI: {c["rsi"]:.1f} | no centralised spot-FX volume\n'
                       f'Illustrative stop: {fmt(pair, stop)} | 2R target: {fmt(pair, target)}\n'
                       'Indicative spot quotes, not executable broker bid/ask. Verify spread, costs, contract size and live levels. Not an order.')
            return f'{pair}:{int(candle_ts)}:{side}', message
    return None


def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat:
        print('Missing Telegram secrets', file=sys.stderr)
        return 1
    state = load_state()
    save_state(state)  # Ensure cache has a file even when no setup is found.
    errors = []
    for symbol in ('ETHUSDT', 'ZECUSDT'):
        try:
            signal = crypto_signal(symbol)
            if signal:
                key, msg = signal
                if state.get(symbol) != key:
                    send_message(token, chat, msg)
                    state[symbol] = key
                    save_state(state)
                    print(f'{symbol}: sent alert {key}')
                else:
                    print(f'{symbol}: duplicate skipped')
            else:
                print(f'{symbol}: no confirmed setup')
        except Exception as exc:
            errors.append(symbol)
            print(f'{symbol}: {type(exc).__name__}: {str(exc)[:160]}', file=sys.stderr)
    if os.getenv('TWELVE_DATA_API_KEY', '').strip():
        for pair in PAIRS:
            try:
                time.sleep(9)
                signal = forex_signal(pair)
                if signal:
                    key, msg = signal
                    if state.get(pair) != key:
                        send_message(token, chat, msg)
                        state[pair] = key
                        save_state(state)
                        print(f'{pair}: sent alert {key}')
                    else:
                        print(f'{pair}: duplicate skipped')
                else:
                    print(f'{pair}: no confirmed setup')
            except Exception as exc:
                errors.append(pair)
                print(f'{pair}: {type(exc).__name__}: {str(exc)[:160]}', file=sys.stderr)
    else:
        print('Forex skipped: Twelve Data secret missing')
    if errors:
        print('Failed markets: ' + ', '.join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

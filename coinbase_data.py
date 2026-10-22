"""Coinbase Exchange public candles adapter for existing bot.py analysis.
No API keys required. Returns Binance-shaped klines to bot.frame_analysis.
"""
import datetime as dt
import requests

API = 'https://api.exchange.coinbase.com/products/{product}/candles'


def candles(symbol, interval, limit=120):
    symbol = symbol.upper()
    if not symbol.endswith('USDT'):
        raise ValueError('Only USDT-quoted symbols supported by this adapter')
    # USD spot is used as a proxy for USDT, not an exact market equivalent.
    product = symbol[:-4] + '-USD'
    if interval not in ('15m', '1h', '4h'):
        raise ValueError('Unsupported interval')
    granularity = 900 if interval == '15m' else 3600
    # Coinbase Exchange limits candles to 300 per request.
    needed = min(300, max(65, limit * (4 if interval == '4h' else 1) + 4))
    now = dt.datetime.now(dt.timezone.utc)
    end = now
    start = end - dt.timedelta(seconds=needed * granularity)
    response = requests.get(
        API.format(product=product),
        params={'granularity': granularity, 'start': start.isoformat(), 'end': end.isoformat()},
        headers={'User-Agent': 'DeborahCryptoAnalyst/1.0', 'Accept': 'application/json'},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError('Unexpected Coinbase candle response')
    # Coinbase candle: [timestamp, low, high, open, close, volume]
    ordered = sorted(data, key=lambda c: c[0])
    # Discard the currently forming interval.
    current_bucket = int(now.timestamp()) // granularity * granularity
    ordered = [c for c in ordered if int(c[0]) < current_bucket]
    if interval == '4h':
        buckets = {}
        for c in ordered:
            key = int(c[0]) // 14400 * 14400
            buckets.setdefault(key, []).append(c)
        # Only full 4-hour groups, avoiding partial bars.
        ordered = []
        for key in sorted(buckets):
            group = sorted(buckets[key], key=lambda c: c[0])
            if len(group) != 4 or [int(x[0]) for x in group] != [key + 3600*i for i in range(4)]:
                continue
            ordered.append([key, min(float(x[1]) for x in group), max(float(x[2]) for x in group), float(group[0][3]), float(group[-1][4]), sum(float(x[5]) for x in group)])
    if len(ordered) < 60:
        raise ValueError(f'Not enough completed Coinbase candles for {product} {interval}: {len(ordered)}')
    return [[int(c[0])*1000, float(c[3]), float(c[2]), float(c[1]), float(c[4]), float(c[5])] for c in ordered[-limit:]]

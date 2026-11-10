"""Forex/gold reports using Twelve Data OHLC. No orders or volume claims."""
import os
import time
import math
from datetime import datetime, timezone
import requests

PAIRS = ('EUR/USD', 'GBP/USD', 'USD/JPY', 'XAU/USD')
API = 'https://api.twelvedata.com/time_series'


def get_candles(pair, interval, size=140):
    key = os.getenv('TWELVE_DATA_API_KEY', '').strip()
    if not key:
        raise ValueError('TWELVE_DATA_API_KEY secret is missing')
    r = requests.get(API, params={'symbol': pair, 'interval': interval, 'outputsize': size,
                                  'timezone': 'UTC', 'apikey': key}, timeout=25)
    r.raise_for_status()
    data = r.json()
    if data.get('status') == 'error' or 'values' not in data:
        raise ValueError('Twelve Data: ' + str(data.get('message', 'no candles'))[:150])
    candles = []
    for v in reversed(data['values']):
        candles.append({'ts': datetime.fromisoformat(v['datetime']).replace(tzinfo=timezone.utc),
                        'o': float(v['open']), 'h': float(v['high']),
                        'l': float(v['low']), 'c': float(v['close'])})
    # Exclude currently forming bars (if returned), based on UTC candle start.
    seconds = {'15min': 900, '1h': 3600, '4h': 14400}[interval]
    now = datetime.now(timezone.utc).timestamp()
    candles = [c for c in candles if c['ts'].timestamp() + seconds <= now]
    if len(candles) < 60:
        raise ValueError(f'Insufficient completed {interval} candles for {pair}: {len(candles)}')
    return candles


def ema(seq, n):
    a = 2/(n+1)
    x = seq[0]
    for v in seq[1:]:
        x = a*v+(1-a)*x
    return x


def rsi(seq, n=14):
    diffs = [seq[i]-seq[i-1] for i in range(1,len(seq))]
    g = sum(max(0,d) for d in diffs[:n])/n
    l = sum(max(0,-d) for d in diffs[:n])/n
    for d in diffs[n:]:
        g = (g*(n-1)+max(0,d))/n
        l = (l*(n-1)+max(0,-d))/n
    if l == 0:
        return 100.0 if g else 50.0
    return 100-100/(1+g/l)


def frame(candles, label):
    closes = [c['c'] for c in candles]
    a, b = ema(closes,20), ema(closes,50)
    trend = 'BULLISH' if a>b and closes[-1]>a else 'BEARISH' if a<b and closes[-1]<a else 'NEUTRAL'
    tr = [max(candles[i]['h']-candles[i]['l'],
              abs(candles[i]['h']-candles[i-1]['c']),
              abs(candles[i]['l']-candles[i-1]['c'])) for i in range(1,len(candles))]
    return {'label': label, 'close': closes[-1], 'rsi': rsi(closes), 'trend': trend,
            'ema20': a, 'ema50': b, 'atr': sum(tr[-14:])/14,
            'high': max(c['h'] for c in candles[-21:-1]),
            'low': min(c['l'] for c in candles[-21:-1]),
            'time': candles[-1]['ts']}


def fmt(pair, x):
    return f'{x:,.3f}' if pair == 'XAU/USD' else f'{x:.3f}' if pair == 'USD/JPY' else f'{x:.5f}'


def analyze(pair):
    if pair not in PAIRS:
        raise ValueError('Unsupported pair')
    frames = []
    # Deliberately space requests to respect Basic plan 8 credits/minute.
    for idx, (interval, label) in enumerate((('4h','4H'),('1h','1H'),('15min','15M'))):
        if idx:
            time.sleep(9)
        frames.append(frame(get_candles(pair,interval),label))
    a,b,c = frames
    bullish = sum(f['trend']=='BULLISH' for f in frames)
    bearish = sum(f['trend']=='BEARISH' for f in frames)
    # This rule uses ONLY completed price candles, not volume.
    long_break = c['close'] > c['high'] and c['rsi'] < 75
    short_break = c['close'] < c['low'] and c['rsi'] > 25
    if bullish >= 2 and long_break:
        status='TRIGGER LONG'
    elif bearish >= 2 and short_break:
        status='TRIGGER SHORT'
    elif bullish >= 2:
        status='WATCH LONG'
    elif bearish >= 2:
        status='WATCH SHORT'
    else:
        status='WAIT'
    pip = .01 if pair=='USD/JPY' else .0001 if pair != 'XAU/USD' else None
    side = 'LONG' if status.endswith('LONG') else 'SHORT' if status.endswith('SHORT') else None
    stop = (c['low'] - .25*c['atr']) if side=='LONG' else (c['high']+.25*c['atr']) if side=='SHORT' else None
    target = c['close'] + 2*(c['close']-stop) if side=='LONG' else c['close']-2*(stop-c['close']) if side=='SHORT' else None
    lines = [f'🧭 Deborah’s Crypto Analyst — {pair}',
             f'Twelve Data indicative spot candles • checked {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}',
             f'Last completed 15M candle: {c["time"]:%Y-%m-%d %H:%M UTC}',
             f'Status: {status}', '', 'Multi-timeframe snapshot']
    for f in frames:
        lines.append(f'• {f["label"]}: {f["trend"]} | close {fmt(pair,f["close"])} | RSI {f["rsi"]:.1f} | EMA20 {"above" if f["ema20"]>f["ema50"] else "below"} EMA50')
    lines += ['', '15M completed-candle checks',
              f'• Range high: {fmt(pair,c["high"])}; range low: {fmt(pair,c["low"])}',
              f'• ATR(14): {fmt(pair,c["atr"])}',
              '• Volume confirmation: unavailable (no centralised spot-FX volume)',
              '• Price-only breakout: ' + ('above range' if long_break else 'below range' if short_break else 'not confirmed')]
    if side:
        lines += ['', f'Illustrative {side} study (NOT an order)',
                  f'• Reference close: {fmt(pair,c["close"])}',
                  f'• Structural stop: {fmt(pair,stop)}',
                  f'• Illustrative 2R target: {fmt(pair,target)}']
        if pip:
            lines.append(f'• Stop distance: {abs(c["close"]-stop)/pip:.1f} pips')
        else:
            lines.append('• Gold stop distance: '+f'{abs(c["close"]-stop):.2f} USD/oz')
    lines += ['', '⚠️ Price feed is indicative, not your broker’s executable bid/ask. No volume validation, spread, slippage, swap, position-size or broker contract calculation. Forex/gold markets may be closed; stale candles must not be traded. Unbacktested rules; verify on your platform. No orders placed.']
    return '\n'.join(lines), status, c['time']

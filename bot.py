import os, sqlite3, math, json
from datetime import datetime, timezone
from typing import List, Dict, Any
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
DB_PATH = os.getenv('DB_PATH', 'trades.db')
STARTING_EQUITY = float(os.getenv('ACCOUNT_EQUITY', '2394'))
RISK_PCT = float(os.getenv('RISK_PERCENT', '1'))
LEVERAGE = float(os.getenv('LEVERAGE', '10'))
BINANCE = 'https://api.binance.com/api/v3/klines'

TIMEFRAMES = [('4h', '4H'), ('1h', '1H'), ('15m', '15M')]

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE IF NOT EXISTS trades (
      id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, symbol TEXT NOT NULL,
      direction TEXT NOT NULL, setup TEXT NOT NULL, timeframe TEXT NOT NULL,
      entry REAL, stop REAL, target REAL, quantity REAL, notional REAL, risk_usd REAL,
      status TEXT NOT NULL DEFAULT 'OPEN', exit_price REAL, gross_pnl REAL, fees REAL DEFAULT 0,
      net_pnl REAL, notes TEXT DEFAULT '', source TEXT DEFAULT 'manual')''')
    conn.commit()
    return conn

def candles(symbol: str, interval: str, limit: int = 120) -> List[List[Any]]:
    r = requests.get(BINANCE, params={'symbol': symbol.upper(), 'interval': interval, 'limit': limit}, timeout=12)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list) or len(data) < 60:
        raise ValueError('Not enough candle data returned.')
    return data

def ema(values, period):
    k = 2/(period+1)
    out=[]; cur=values[0]
    for v in values:
        cur = v*k + cur*(1-k); out.append(cur)
    return out

def rsi(values, period=14):
    if len(values) < period+1: return 50.0
    diffs=[values[i]-values[i-1] for i in range(1,len(values))]
    gains=[max(x,0) for x in diffs]; losses=[max(-x,0) for x in diffs]
    ag=sum(gains[:period])/period; al=sum(losses[:period])/period
    for i in range(period,len(diffs)):
        ag=(ag*(period-1)+gains[i])/period; al=(al*(period-1)+losses[i])/period
    if al == 0: return 100.0
    return 100-(100/(1+ag/al))

def frame_analysis(symbol, interval, label):
    raw=candles(symbol, interval)
    highs=[float(x[2]) for x in raw]; lows=[float(x[3]) for x in raw]
    closes=[float(x[4]) for x in raw]; vols=[float(x[5]) for x in raw]
    e20=ema(closes,20)[-1]; e50=ema(closes,50)[-1]
    rr=rsi(closes)
    trs=[]
    for i in range(1,len(raw)):
        h,l,pc=highs[i],lows[i],closes[i-1]
        trs.append(max(h-l,abs(h-pc),abs(l-pc)))
    atr=sum(trs[-14:])/min(14,len(trs))
    avgvol=sum(vols[-21:-1])/20 if len(vols)>=21 else sum(vols)/len(vols)
    lastvol=vols[-1]
    trend='BULLISH' if e20>e50 and closes[-1]>e20 else ('BEARISH' if e20<e50 and closes[-1]<e20 else 'NEUTRAL')
    # Use completed candles for trigger logic; current last candle can be in progress on Binance.
    prevclose=closes[-2]; prevhigh=highs[-2]; prevlow=lows[-2]
    swing_high=max(highs[-21:-1]); swing_low=min(lows[-21:-1])
    return {'label':label,'interval':interval,'close':closes[-1],'prevclose':prevclose,'high':highs[-1], 'low':lows[-1],
            'ema20':e20,'ema50':e50,'rsi':rr,'atr':atr,'trend':trend,'swing_high':swing_high,'swing_low':swing_low,
            'vol_ratio':lastvol/avgvol if avgvol else 0,'prevhigh':prevhigh,'prevlow':prevlow}

def fmt_price(x):
    if x >= 1000: return f'{x:,.2f}'
    if x >= 1: return f'{x:.4f}'.rstrip('0').rstrip('.')
    return f'{x:.8f}'.rstrip('0').rstrip('.')

def analyze(symbol):
    frames=[frame_analysis(symbol, interval, label) for interval,label in TIMEFRAMES]
    a,b,c=frames
    bulls=sum(x['trend']=='BULLISH' for x in frames); bears=sum(x['trend']=='BEARISH' for x in frames)
    # Conservative conditional triggers; not an order signal.
    long_trigger=(c['close'] > c['swing_high'] and c['vol_ratio'] >= 1.1 and c['rsi'] < 75)
    short_trigger=(c['close'] < c['swing_low'] and c['vol_ratio'] >= 1.1 and c['rsi'] > 25)
    if bulls>=2 and long_trigger: status='TRIGGER CHECK: possible LONG confirmation'
    elif bears>=2 and short_trigger: status='TRIGGER CHECK: possible SHORT confirmation'
    elif bulls>=2: status='WATCH LONG: broader bias bullish; entry trigger not confirmed'
    elif bears>=2: status='WATCH SHORT: broader bias bearish; entry trigger not confirmed'
    else: status='WAIT: timeframes are mixed or neutral'
    # Structural reference stops, using 15m recent range and 1 ATR buffer. For study only.
    long_stop=c['swing_low']-0.25*c['atr']
    short_stop=c['swing_high']+0.25*c['atr']
    risk=STARTING_EQUITY*RISK_PCT/100
    long_dist=(c['close']-long_stop)/c['close'] if c['close']>long_stop else None
    short_dist=(short_stop-c['close'])/c['close'] if short_stop>c['close'] else None
    def calc(dist):
        if not dist or dist<=0: return None,None
        notional=risk/dist
        return notional, notional/c['close']
    ln,lq=calc(long_dist); sn,sq=calc(short_dist)
    out=[f'🧭 Deborah’s Crypto Analyst — {symbol.upper()}', f'Public Binance SPOT candles • checked {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}', '', f'**Status:** {status}', '', '**Multi-timeframe snapshot**']
    for x in frames:
        out.append(f"• {x['label']}: {x['trend']} | close {fmt_price(x['close'])} | RSI {x['rsi']:.1f} | EMA20 {'above' if x['ema20']>x['ema50'] else 'below'} EMA50")
    out += ['', '**15M trigger checks**', f"• Recent-range high: {fmt_price(c['swing_high'])}; low: {fmt_price(c['swing_low'])}", f"• Volume vs 20-candle average: {c['vol_ratio']:.2f}×", '• Breakout check uses the latest returned candle, which may still be forming. Confirm on a completed candle before acting.', '', '**Illustrative structure-based risk math**', f'• Equity setting: ${STARTING_EQUITY:,.2f} | risk setting: {RISK_PCT:.2f}% = ${risk:.2f}', f'• Reference price: {fmt_price(c["close"])} (not a guaranteed executable price)']
    if ln: out.append(f'• LONG study stop: {fmt_price(long_stop)} | estimated notional ${ln:,.2f} | quantity {lq:.6g} | approximate margin at {LEVERAGE:g}×: ${ln/LEVERAGE:,.2f}')
    if sn: out.append(f'• SHORT study stop: {fmt_price(short_stop)} | estimated notional ${sn:,.2f} | quantity {sq:.6g} | approximate margin at {LEVERAGE:g}×: ${sn/LEVERAGE:,.2f}')
    out += ['', '⚠️ Educational conditional analysis, not a trade instruction. Binance SPOT data is not Margex perpetual-futures data. The latest candle may be incomplete; rules are simple heuristics and have not been backtested. Costs, funding, slippage, contract rules and liquidation are not fully modelled. Verify all levels manually; no orders are placed.']
    return '\n'.join(out), frames

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome to Deborah’s Crypto Analyst 💜\n\nSeparate companion bot; your existing bot is untouched.\n\nCommands:\n/analyze ZECUSDT — multi-timeframe snapshot\n/log SYMBOL LONG|SHORT ENTRY STOP TARGET SETUP — log an OPEN trade\n/close ID EXIT FEES — close a logged trade\n/journal — latest journal entries\n/stats — realised journal statistics\n/settings — risk settings\n/help — command help\n\nData source: public Binance spot candles. No orders are placed.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f'Account equity setting: ${STARTING_EQUITY:,.2f}\nRisk setting: {RISK_PCT:.2f}% (${STARTING_EQUITY*RISK_PCT/100:.2f} planned risk)\nLeverage assumption: {LEVERAGE:g}×\nThese are environment settings, not read from your exchange account.')

async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol=context.args[0].upper() if context.args else 'ZECUSDT'
    if not symbol.isalnum() or len(symbol)>20:
        await update.message.reply_text('Use a symbol such as ZECUSDT or ETHUSDT.'); return
    try:
        report,_=analyze(symbol)
        # Telegram messages max 4096 chars; this report is short enough.
        await update.message.reply_text(report)
    except Exception as e:
        await update.message.reply_text(f'Could not analyse {symbol}. Check the symbol or try again later. Details: {str(e)[:180]}')

async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args)<6:
        await update.message.reply_text('Format: /log SYMBOL LONG|SHORT ENTRY STOP TARGET SETUP\nExample: /log ZECUSDT LONG 1350 1328 1395 breakout\nUse your actual planned levels. This logs an OPEN trade; it does not place an order.')
        return
    symbol,direction=context.args[0].upper(),context.args[1].upper()
    try:
        entry,stop,target=map(float,context.args[2:5]); setup=' '.join(context.args[5:])
        if direction not in ('LONG','SHORT') or min(entry,stop,target)<=0: raise ValueError()
        if (direction=='LONG' and not stop<entry<target) or (direction=='SHORT' and not target<entry<stop):
            await update.message.reply_text('Level order looks inconsistent. LONG expects STOP < ENTRY < TARGET; SHORT expects TARGET < ENTRY < STOP.'); return
        dist=abs(entry-stop)/entry
        risk=STARTING_EQUITY*RISK_PCT/100
        notional=risk/dist
        qty=notional/entry
        conn=db(); cur=conn.execute('INSERT INTO trades(created_at,symbol,direction,setup,timeframe,entry,stop,target,quantity,notional,risk_usd,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (datetime.now(timezone.utc).isoformat(),symbol,direction,setup,'manual',entry,stop,target,qty,notional,risk,'OPEN'))
        conn.commit(); tid=cur.lastrowid; conn.close()
        await update.message.reply_text(f'Logged OPEN trade #{tid} (no order placed)\n{symbol} {direction} • setup: {setup}\nEntry {entry:g} | stop {stop:g} | target {target:g}\nPlanned risk ${risk:.2f} • stop distance {dist*100:.2f}%\nEstimated notional ${notional:,.2f} • quantity {qty:.6g}\nVerify sizing against your exchange contract and costs.')
    except Exception:
        await update.message.reply_text('Could not parse those values. Use: /log SYMBOL LONG|SHORT ENTRY STOP TARGET SETUP')

async def close_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args)<2:
        await update.message.reply_text('Format: /close TRADE_ID EXIT_PRICE [FEES_TOTAL]\nExample: /close 3 1392.5 2.10'); return
    try:
        tid=int(context.args[0]); exitp=float(context.args[1]); fees=float(context.args[2]) if len(context.args)>2 else 0.0
        conn=db(); row=conn.execute('SELECT * FROM trades WHERE id=? AND status="OPEN"',(tid,)).fetchone()
        if not row: conn.close(); await update.message.reply_text('No OPEN trade found with that ID.'); return
        sign=1 if row['direction']=='LONG' else -1
        gross=(exitp-row['entry'])*row['quantity']*sign
        net=gross-fees
        conn.execute('UPDATE trades SET status="CLOSED",exit_price=?,gross_pnl=?,fees=?,net_pnl=? WHERE id=?',(exitp,gross,fees,net,tid)); conn.commit(); conn.close()
        rmult=net/row['risk_usd'] if row['risk_usd'] else 0
        await update.message.reply_text(f'Closed journal trade #{tid}\nGross P&L: ${gross:+.2f}\nFees entered: ${fees:.2f}\nNet P&L: ${net:+.2f}\nOutcome: {rmult:+.2f}R\nReminder: this is a journal calculation, not exchange-verified P&L.')
    except Exception:
        await update.message.reply_text('Could not parse values. Use: /close TRADE_ID EXIT_PRICE [FEES_TOTAL]')

async def journal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn=db(); rows=conn.execute('SELECT * FROM trades ORDER BY id DESC LIMIT 10').fetchall(); conn.close()
    if not rows: await update.message.reply_text('Your journal is empty. Use /log to record a planned or executed trade.'); return
    lines=['📒 Latest journal entries']
    for r in rows:
        result=f" | net ${r['net_pnl']:+.2f}" if r['net_pnl'] is not None else ''
        lines.append(f"#{r['id']} {r['symbol']} {r['direction']} {r['status']} | entry {r['entry']:g} | stop {r['stop']:g}{result} | {r['setup']}")
    await update.message.reply_text('\n'.join(lines))

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn=db(); rows=conn.execute('SELECT net_pnl,gross_pnl,risk_usd FROM trades WHERE status="CLOSED" AND net_pnl IS NOT NULL').fetchall(); conn.close()
    if not rows: await update.message.reply_text('No closed trades in the journal yet. Log trades and close them with /close.'); return
    pnls=[float(r['net_pnl']) for r in rows]; wins=[p for p in pnls if p>0]; losses=[p for p in pnls if p<0]
    gross_profit=sum(wins); gross_loss=abs(sum(losses)); pf=gross_profit/gross_loss if gross_loss else (float('inf') if gross_profit else 0)
    expectancy=sum(pnls)/len(pnls); wr=100*len(wins)/len(pnls)
    rs=[float(r['net_pnl'])/float(r['risk_usd']) for r in rows if r['risk_usd']]
    await update.message.reply_text(f'📈 Closed-trade journal stats (n={len(pnls)})\nWin rate: {wr:.1f}%\nNet P&L: ${sum(pnls):+.2f}\nAverage net P&L/trade: ${expectancy:+.2f}\nAverage winner: ${sum(wins)/len(wins):.2f}' if wins else f'📈 Closed-trade journal stats (n={len(pnls)})\nWin rate: {wr:.1f}%\nNet P&L: ${sum(pnls):+.2f}\nAverage net P&L/trade: ${expectancy:+.2f}\nNo winning trades recorded yet.' + f'\nProfit factor: {pf:.2f}\nAverage outcome: {sum(rs)/len(rs):+.2f}R' if rs else f'📈 Closed-trade journal stats (n={len(pnls)})\nWin rate: {wr:.1f}%\nNet P&L: ${sum(pnls):+.2f}\nAverage net P&L/trade: ${expectancy:+.2f}\nProfit factor: {pf:.2f}')

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print('Bot error:', context.error)

def main():
    if not BOT_TOKEN:
        raise SystemExit('Set TELEGRAM_BOT_TOKEN in the environment. Never hard-code or share the token.')
    db().close()
    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start',start)); app.add_handler(CommandHandler('help',help_cmd))
    app.add_handler(CommandHandler('analyze',analyze_cmd)); app.add_handler(CommandHandler('log',log_cmd))
    app.add_handler(CommandHandler('close',close_cmd)); app.add_handler(CommandHandler('journal',journal_cmd))
    app.add_handler(CommandHandler('stats',stats_cmd)); app.add_handler(CommandHandler('settings',settings))
    app.add_error_handler(error_handler)
    print('Deborah Crypto Analyst is running.')
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__': main()

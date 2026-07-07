"""
run_positional_stocks_backtest.py
-----------------------------------
Backtests the positional_stocks.py engine (long-only technical investment
on large-caps) against 2 years of real NSE daily OHLCV data pulled via
yfinance, reporting results for the last 6 months. Mirrors the
run_june_positional_backtest.py pattern used for the weekly options module.

Data: data/stock_daily/{SYMBOL}.csv (2yr daily bars, downloaded via
yfinance -- see data/stock_daily/README or the download snippet in this
file's git history). Uses the full 2yr history as warmup so indicators
like Golden Cross (needs 200 daily bars) and Cup & Handle are live for
the last 6 months, but only counts trades in the reporting window.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.chartedge_core.models import Candle
from services.chartedge_core.positional_stocks import compute_stock_signal

SYMBOLS = ["RELIANCE", "HDFCBANK", "TCS", "INFY", "ICICIBANK", "SBIN", "BHARTIARTL",
           "ITC", "KOTAKBANK", "LT", "AXISBANK", "BAJFINANCE", "MARUTI", "ASIANPAINT",
           "HCLTECH", "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO", "ADANIENT"]
CAPITAL = 100_000.0
MAX_POSITIONS = 8
STOP_LOSS_PCT = 4.0
TARGET_PCT = 12.0
BUY_THRESHOLD = 0.35
SELL_THRESHOLD = -0.35
MIN_ADX = 25.0
REPORT_MONTHS = 12
REQUIRE_TREND_GATE = True  # hard AND-gate: ema_ribbon + supertrend both bullish, not just weighted avg


def load_daily_candles(symbol: str) -> list[Candle]:
    df = pd.read_csv(f"data/stock_daily/{symbol}.csv", index_col=0, parse_dates=True)
    candles = []
    for ts, row in df.iterrows():
        candles.append(Candle(
            time=ts.to_pydatetime(), instrument=symbol, timeframe="1D",
            open=float(row["Open"]), high=float(row["High"]), low=float(row["Low"]),
            close=float(row["Close"]), volume=int(row["Volume"]),
        ))
    return candles


TRAIL_ARM_PCT = 3.0    # once up this much, stop trusting weak SELL_SIGNAL exits
TRAIL_KEEP_FRAC = 0.5  # trailing stop locks in this fraction of peak gain


class BacktestPosition:
    def __init__(self, symbol, entry_date, entry_price, quantity):
        self.symbol = symbol
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.quantity = quantity
        self.exit_date = None
        self.exit_price = None
        self.exit_reason = None
        self.pnl = 0.0
        self.pnl_pct = 0.0
        self.peak_pnl_pct = 0.0
        self.trail_armed = False


def run_backtest():
    all_candles = {sym: load_daily_candles(sym) for sym in SYMBOLS}
    n_days = min(len(c) for c in all_candles.values())
    print(f"Loaded {n_days} daily bars per symbol ({SYMBOLS})")

    report_start = all_candles[SYMBOLS[0]][-1].time.date() - timedelta(days=REPORT_MONTHS * 30)

    open_positions: dict[str, BacktestPosition] = {}
    closed_positions: list[BacktestPosition] = []
    capital = CAPITAL
    slot_capital = capital / MAX_POSITIONS

    # walk forward day by day (index 60 onward, need warmup for indicators)
    for i in range(60, n_days):
        today = all_candles[SYMBOLS[0]][i].time.date()

        for symbol in SYMBOLS:
            daily_so_far = all_candles[symbol][: i + 1]
            price = daily_so_far[-1].close
            score, indicators = compute_stock_signal(daily_so_far, {})
            adx_value = indicators["adx"].value

            if symbol in open_positions:
                pos = open_positions[symbol]
                pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
                pos.peak_pnl_pct = max(pos.peak_pnl_pct, pnl_pct)
                if pos.peak_pnl_pct >= TRAIL_ARM_PCT:
                    pos.trail_armed = True

                reason = None
                if pos.trail_armed:
                    trail_floor = pos.peak_pnl_pct * TRAIL_KEEP_FRAC
                    if pnl_pct <= trail_floor:
                        reason = "TRAILING_STOP"
                    elif pnl_pct >= TARGET_PCT:
                        reason = "TARGET"
                else:
                    if pnl_pct <= -STOP_LOSS_PCT:
                        reason = "STOP_LOSS"
                    elif pnl_pct >= TARGET_PCT:
                        reason = "TARGET"
                    elif score <= SELL_THRESHOLD:
                        reason = "SELL_SIGNAL"
                if reason:
                    pos.exit_date = today
                    pos.exit_price = price
                    pos.exit_reason = reason
                    pos.pnl = (price - pos.entry_price) * pos.quantity
                    pos.pnl_pct = pnl_pct
                    closed_positions.append(pos)
                    del open_positions[symbol]
            else:
                trend_ok = True
                if REQUIRE_TREND_GATE:
                    trend_ok = indicators["ema_ribbon"].vote == 1 and indicators["supertrend"].vote == 1
                if (score >= BUY_THRESHOLD and adx_value >= MIN_ADX and trend_ok
                        and len(open_positions) < MAX_POSITIONS and price > 0):
                    qty = int(slot_capital // price)
                    if qty > 0:
                        open_positions[symbol] = BacktestPosition(symbol, today, price, qty)

    # snapshot still-open positions for reporting, then force-close copies for PnL stats
    still_open = dict(open_positions)
    for symbol, pos in still_open.items():
        price = all_candles[symbol][-1].close
        pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
        pos.exit_date = all_candles[symbol][-1].time.date()
        pos.exit_price = price
        pos.exit_reason = "STILL_OPEN_MARK"
        pos.pnl = (price - pos.entry_price) * pos.quantity
        pos.pnl_pct = pnl_pct
        closed_positions.append(pos)

    report_positions = [p for p in closed_positions if p.entry_date >= report_start]
    # entry frequency is low (buy_threshold=0.50 is selective); if the last-6mo
    # window happens to have zero new entries by chance, fall back to reporting
    # the full 2yr set so the backtest is still informative.
    reporting_full_history = len(report_positions) == 0 and len(closed_positions) > 0
    if reporting_full_history:
        report_positions = closed_positions
        report_start = all_candles[SYMBOLS[0]][60].time.date()

    print(f"\n{'='*70}")
    label = "full 2yr history (no entries in last 6mo window)" if reporting_full_history else f"last {REPORT_MONTHS} months"
    print(f"POSITIONAL STOCKS BACKTEST -- {label} "
          f"({report_start} to {all_candles[SYMBOLS[0]][-1].time.date()})")
    print(f"{'='*70}")
    print(f"Total closed positions (full 2yr, incl. warmup entries): {len(closed_positions)}")
    print(f"Positions in reporting window: {len(report_positions)}\n")

    if report_positions:
        wins = sum(1 for p in report_positions if p.pnl > 0)
        total_pnl = sum(p.pnl for p in report_positions)
        gross_win = sum(p.pnl for p in report_positions if p.pnl > 0)
        gross_loss = abs(sum(p.pnl for p in report_positions if p.pnl <= 0))
        print(f"Trades: {len(report_positions)} | Wins: {wins} ({wins/len(report_positions)*100:.1f}%)")
        print(f"Net PnL: Rs {total_pnl:,.2f} | Return: {total_pnl/CAPITAL*100:.2f}% on Rs {CAPITAL:,.0f}")
        print(f"Gross win: Rs {gross_win:,.2f} | Gross loss: Rs {gross_loss:,.2f} | "
              f"Profit factor: {gross_win/gross_loss:.2f}x" if gross_loss else "Profit factor: inf")
        print("\nPer-symbol breakdown:")
        for sym in SYMBOLS:
            sym_trades = [p for p in report_positions if p.symbol == sym]
            if not sym_trades:
                print(f"  {sym}: no trades")
                continue
            sym_pnl = sum(p.pnl for p in sym_trades)
            sym_wins = sum(1 for p in sym_trades if p.pnl > 0)
            print(f"  {sym}: {len(sym_trades)} trades, {sym_wins} wins, PnL Rs {sym_pnl:,.2f}")

        print("\nTrade log:")
        for p in sorted(report_positions, key=lambda p: p.entry_date):
            print(f"  {p.symbol:10s} {p.entry_date} @ {p.entry_price:.2f} -> "
                  f"{p.exit_date} @ {p.exit_price:.2f} | {p.exit_reason:15s} | "
                  f"PnL {p.pnl:+.2f} ({p.pnl_pct:+.2f}%)")
    else:
        print("No trades in reporting window.")

    print(f"\nOpen positions at end of backtest: {len(still_open)}")
    for sym, pos in still_open.items():
        print(f"  {sym}: entered {pos.entry_date} @ {pos.entry_price:.2f}, "
              f"unrealized {pos.pnl:+.2f} ({pos.pnl_pct:+.2f}%)")


if __name__ == "__main__":
    run_backtest()

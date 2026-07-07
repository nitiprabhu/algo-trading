"""
screen_positional_stocks.py
-----------------------------
Per-symbol screen: runs the positional_stocks engine logic independently
on each candidate stock over 2yr, reports trades/win%/PnL/return per
symbol. Use this to decide which stocks to keep in positional_stocks_risk
.symbols -- drop net-negative names, keep net-positive ones.
"""
from __future__ import annotations

import glob
import os
from datetime import date
from statistics import mean

import pandas as pd

from services.chartedge_core.models import Candle
from services.chartedge_core.positional_stocks import compute_stock_signal

STOP_LOSS_PCT = 4.0
TARGET_PCT = 12.0
BUY_THRESHOLD = 0.35
SELL_THRESHOLD = -0.35
MIN_ADX = 25.0
TRAIL_ARM_PCT = 3.0
TRAIL_KEEP_FRAC = 0.5
PER_SYMBOL_CAPITAL = 100_000.0  # isolated capital per symbol for fair comparison


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


class Pos:
    def __init__(self, entry_date, entry_price, qty):
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.qty = qty
        self.peak = 0.0
        self.pnl = 0.0
        self.pnl_pct = 0.0
        self.reason = None


def run_symbol(symbol: str) -> dict:
    candles = load_daily_candles(symbol)
    n = len(candles)
    if n < 100:
        return {"symbol": symbol, "trades": 0, "error": "insufficient data"}

    open_pos: Pos | None = None
    closed: list[Pos] = []

    for i in range(60, n):
        c = candles[: i + 1]
        price = c[-1].close
        score, ind = compute_stock_signal(c, {})
        adx_v = ind["adx"].value
        trend_ok = ind["ema_ribbon"].vote == 1 and ind["supertrend"].vote == 1

        if open_pos:
            pnl_pct = (price - open_pos.entry_price) / open_pos.entry_price * 100
            open_pos.peak = max(open_pos.peak, pnl_pct)
            armed = open_pos.peak >= TRAIL_ARM_PCT
            reason = None
            if armed:
                floor = open_pos.peak * TRAIL_KEEP_FRAC
                if pnl_pct <= floor:
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
                open_pos.pnl = (price - open_pos.entry_price) * open_pos.qty
                open_pos.pnl_pct = pnl_pct
                open_pos.reason = reason
                closed.append(open_pos)
                open_pos = None
        else:
            if score >= BUY_THRESHOLD and adx_v >= MIN_ADX and trend_ok and price > 0:
                qty = int(PER_SYMBOL_CAPITAL // price)
                if qty > 0:
                    open_pos = Pos(c[-1].time.date(), price, qty)

    if open_pos:
        price = candles[-1].close
        pnl_pct = (price - open_pos.entry_price) / open_pos.entry_price * 100
        open_pos.pnl = (price - open_pos.entry_price) * open_pos.qty
        open_pos.pnl_pct = pnl_pct
        open_pos.reason = "STILL_OPEN"
        closed.append(open_pos)

    if not closed:
        return {"symbol": symbol, "trades": 0}

    wins = sum(1 for p in closed if p.pnl > 0)
    total = sum(p.pnl for p in closed)
    return {
        "symbol": symbol,
        "trades": len(closed),
        "wins": wins,
        "win_pct": round(wins / len(closed) * 100, 1),
        "net_pnl": round(total, 2),
        "return_pct": round(total / PER_SYMBOL_CAPITAL * 100, 2),
    }


def main():
    csv_files = sorted(glob.glob("data/stock_daily/*.csv"))
    symbols = [os.path.splitext(os.path.basename(f))[0] for f in csv_files]
    print(f"Screening {len(symbols)} symbols over 2yr...\n")

    results = [run_symbol(s) for s in symbols]
    results = [r for r in results if r.get("trades", 0) > 0]
    results.sort(key=lambda r: r["return_pct"], reverse=True)

    print(f"{'Symbol':<12} {'Trades':>7} {'Win%':>6} {'NetPnL':>10} {'Return%':>8}")
    print("-" * 50)
    for r in results:
        print(f"{r['symbol']:<12} {r['trades']:>7} {r['win_pct']:>6} {r['net_pnl']:>10,.2f} {r['return_pct']:>8.2f}")

    winners = [r for r in results if r["return_pct"] > 0]
    losers = [r for r in results if r["return_pct"] <= 0]
    print(f"\n{'='*50}")
    print(f"WINNERS ({len(winners)}): {[r['symbol'] for r in winners]}")
    print(f"\nLOSERS/FLAT ({len(losers)}): {[r['symbol'] for r in losers]}")
    print(f"\nAvg return% across winners: {mean(r['return_pct'] for r in winners):.2f}" if winners else "")


if __name__ == "__main__":
    main()

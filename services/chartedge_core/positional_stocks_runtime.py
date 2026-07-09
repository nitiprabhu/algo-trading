"""
positional_stocks_runtime.py
-----------------------------
Live wiring for positional_stocks.py. Fully decoupled from the INDmoney/
indstocks intraday feed -- fetches daily OHLCV directly from yfinance
(NSE tickers, e.g. RELIANCE.NS), which needs no API token/auth and never
expires, so this module runs unattended with zero daily intervention.
Runs genuinely once per calendar day, after a post-close cutoff (default
15:35 IST) -- daily-swing technical investment, not intraday trading.
Computes confluence and calls the engine's BUY-only maybe_enter() /
check_exit(). Sends Telegram alerts tagged "[POSITIONAL STOCKS]" via the
same global notifier used elsewhere, distinct from the options positional
module's "[POSITIONAL]" tag.
"""
from __future__ import annotations

from datetime import datetime, date, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from services.chartedge_core.models import Candle
from services.chartedge_core.positional_stocks import (
    PositionalStocksEngine, compute_stock_signal,
)

IST = ZoneInfo("Asia/Kolkata")

# yfinance NSE tickers append .NS; extend as symbols are added.
YFINANCE_TICKER_SUFFIX = ".NS"


def _yf_ticker(symbol: str) -> str:
    return f"{symbol}{YFINANCE_TICKER_SUFFIX}"


def fetch_daily_candles(symbol: str, period: str = "1y") -> list[Candle]:
    """Pull daily OHLCV from yfinance -- no token, no auth, no expiry.
    period="1y" is enough for most indicators here; Golden Cross/Cup&Handle
    need ~200+ bars so a longer period helps once accumulated."""
    ticker = _yf_ticker(symbol)
    df = yf.download(ticker, period=period, interval="1d", progress=False)
    if df.empty:
        return []
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    candles = []
    for ts, row in df.iterrows():
        candles.append(Candle(
            time=ts.to_pydatetime(), instrument=symbol, timeframe="1D",
            open=float(row["Open"]), high=float(row["High"]), low=float(row["Low"]),
            close=float(row["Close"]), volume=int(row["Volume"]),
        ))
    return candles


class PositionalStocksRuntime:
    def __init__(self, engine: PositionalStocksEngine, config: dict):
        self.engine = engine
        self.config = config
        self._last_check_date: date | None = None

    async def check_once_per_day(self) -> None:
        now = datetime.now(IST)
        today = now.date()

        if self._last_check_date == today:
            return
        cutoff = self.config.get("check_after_time", "15:35")
        cutoff_h, cutoff_m = (int(x) for x in cutoff.split(":"))
        if now.time() < dtime(cutoff_h, cutoff_m):
            return

        symbols: list[str] = self.config.get("symbols", [])
        buy_threshold = self.config.get("buy_threshold", 0.35)
        sell_threshold = self.config.get("sell_threshold", -0.35)
        min_adx = self.config.get("min_adx", 25.0)
        require_trend_gate = self.config.get("require_trend_gate", True)
        trail_arm_pct = self.config.get("trail_arm_pct", 3.0)
        trail_keep_frac = self.config.get("trail_keep_frac", 0.5)
        weights = self.config.get("indicator_weights", {})
        yf_period = self.config.get("yfinance_period", "1y")

        for symbol in symbols:
            try:
                daily = fetch_daily_candles(symbol, period=yf_period)
            except Exception as e:
                print(f"[Positional Stocks] yfinance fetch failed for {symbol}: {e}")
                continue
            if len(daily) < 20:
                continue  # not enough daily history yet for any indicator to be meaningful

            score, indicators = compute_stock_signal(daily, weights.get(symbol, {}))
            price = daily[-1].close
            adx_value = indicators["adx"].value

            if symbol in self.engine.open_positions:
                closed = self.engine.check_exit(
                    symbol, today, price, score, sell_threshold,
                    trail_arm_pct=trail_arm_pct, trail_keep_frac=trail_keep_frac,
                )
                if closed:
                    await self._notify_exit(closed)
            else:
                trend_confirmed = True
                if require_trend_gate:
                    trend_confirmed = indicators["ema_ribbon"].vote == 1 and indicators["supertrend"].vote == 1
                opened = self.engine.maybe_enter(
                    symbol, today, price, score, buy_threshold, adx_value, min_adx,
                    trend_confirmed=trend_confirmed,
                )
                if opened:
                    await self._notify_entry(opened, score)

        self._last_check_date = today

    async def _notify_entry(self, position, score: float) -> None:
        from services.chartedge_core.telegram import notifier
        msg = (
            f"[POSITIONAL STOCKS] BUY {position.symbol}\n\n"
            f"Entry: {position.entry_date} @ {position.entry_price}\n"
            f"Quantity: {position.quantity}\n"
            f"Confluence score: {score:.3f}"
        )
        await notifier.send_message(msg)

    async def _notify_exit(self, position) -> None:
        from services.chartedge_core.telegram import notifier
        emoji = "profit" if position.pnl >= 0 else "loss"
        msg = (
            f"[POSITIONAL STOCKS] SELL {position.symbol} ({emoji})\n\n"
            f"Reason: {position.exit_reason}\n"
            f"Entry: {position.entry_price} -> Exit: {position.exit_price}\n"
            f"PnL: {position.pnl:+.2f} ({position.pnl_pct:+.2f}%)"
        )
        await notifier.send_message(msg)

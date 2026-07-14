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

    async def check_once_per_day(self, force: bool = False) -> dict:
        """Run one analysis pass over all configured symbols.

        force=True bypasses the once-per-day guard and the post-close cutoff
        time, for manual/external triggers (e.g. a GitHub Actions workflow).
        Returns a summary dict of what happened this run.
        """
        now = datetime.now(IST)
        today = now.date()

        if not force:
            if self._last_check_date == today:
                return {"ran": False, "reason": "already_checked_today"}
            cutoff = self.config.get("check_after_time", "15:35")
            cutoff_h, cutoff_m = (int(x) for x in cutoff.split(":"))
            if now.time() < dtime(cutoff_h, cutoff_m):
                return {"ran": False, "reason": "before_cutoff_time"}

        entries: list[str] = []
        exits: list[str] = []
        rotations: list[str] = []
        symbols: list[str] = self.config.get("symbols", [])
        buy_threshold = self.config.get("buy_threshold", 0.35)
        sell_threshold = self.config.get("sell_threshold", -0.35)
        min_adx = self.config.get("min_adx", 25.0)
        require_trend_gate = self.config.get("require_trend_gate", True)
        trail_arm_pct = self.config.get("trail_arm_pct", 3.0)
        trail_keep_frac = self.config.get("trail_keep_frac", 0.5)
        allow_rotation = self.config.get("allow_rotation", False)
        rotation_margin = self.config.get("rotation_margin", 0.15)
        weights = self.config.get("indicator_weights", {})
        yf_period = self.config.get("yfinance_period", "1y")

        # scores/prices for every configured symbol computed this run -- reused by the
        # rotation pass below so a blocked-by-capacity BUY can compare against the
        # weakest currently open position without re-fetching/re-scoring anything.
        scores_today: dict[str, float] = {}
        prices_today: dict[str, float] = {}
        pending_rotation_candidates: list[tuple[str, float]] = []

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
            scores_today[symbol] = score
            prices_today[symbol] = price

            if symbol in self.engine.open_positions:
                closed = self.engine.check_exit(
                    symbol, today, price, score, sell_threshold,
                    trail_arm_pct=trail_arm_pct, trail_keep_frac=trail_keep_frac,
                )
                if closed:
                    await self._notify_exit(closed)
                    exits.append(symbol)
            else:
                trend_confirmed = True
                if require_trend_gate:
                    trend_confirmed = indicators["ema_ribbon"].vote == 1 and indicators["supertrend"].vote == 1
                qualifies = score >= buy_threshold and adx_value >= min_adx and trend_confirmed
                opened = self.engine.maybe_enter(
                    symbol, today, price, score, buy_threshold, adx_value, min_adx,
                    trend_confirmed=trend_confirmed,
                )
                if opened:
                    await self._notify_entry(opened, score)
                    entries.append(symbol)
                elif qualifies and allow_rotation:
                    # fully qualifies but blocked purely by pool capacity/capital --
                    # defer to the rotation pass below, once every symbol's score is known.
                    pending_rotation_candidates.append((symbol, score))

        if allow_rotation and pending_rotation_candidates:
            # strongest candidate first -- if it doesn't clear the rotation margin
            # against the weakest holding, weaker candidates won't either.
            pending_rotation_candidates.sort(key=lambda x: x[1], reverse=True)
            for symbol, score in pending_rotation_candidates:
                result = self.engine.maybe_rotate_and_enter(
                    symbol, today, prices_today[symbol], score, buy_threshold,
                    open_scores=scores_today, open_prices=prices_today,
                    trail_arm_pct=trail_arm_pct, rotation_margin=rotation_margin,
                )
                if result:
                    closed, opened = result
                    await self._notify_rotation(closed, opened, score)
                    exits.append(closed.symbol)
                    entries.append(symbol)
                    rotations.append(f"{closed.symbol}->{symbol}")

        self._last_check_date = today
        return {
            "ran": True,
            "checked_symbols": symbols,
            "entries": entries,
            "exits": exits,
            "rotations": rotations,
            "forced": force,
        }

    async def _notify_entry(self, position, score: float) -> None:
        from services.chartedge_core.telegram import notifier
        tag = "POSITIONAL STOCKS" if self.engine.pool == "largecap" else f"POSITIONAL STOCKS {self.engine.pool.upper()}"
        msg = (
            f"[{tag}] BUY {position.symbol}\n\n"
            f"Entry: {position.entry_date} @ {position.entry_price}\n"
            f"Quantity: {position.quantity}\n"
            f"Confluence score: {score:.3f}"
        )
        await notifier.send_message(msg)

    async def _notify_rotation(self, closed, opened, new_score: float) -> None:
        from services.chartedge_core.telegram import notifier
        tag = "POSITIONAL STOCKS" if self.engine.pool == "largecap" else f"POSITIONAL STOCKS {self.engine.pool.upper()}"
        msg = (
            f"[{tag}] ROTATED {closed.symbol} -> {opened.symbol}\n\n"
            f"Sold {closed.symbol}: {closed.entry_price} -> {closed.exit_price} "
            f"(PnL {closed.pnl:+.2f} / {closed.pnl_pct:+.2f}%)\n"
            f"Bought {opened.symbol}: {opened.entry_date} @ {opened.entry_price} "
            f"x{opened.quantity} (score {new_score:.3f})\n"
            f"Reason: pool fully deployed, new signal outscored weakest holding"
        )
        await notifier.send_message(msg)

    async def _notify_exit(self, position) -> None:
        from services.chartedge_core.telegram import notifier
        tag = "POSITIONAL STOCKS" if self.engine.pool == "largecap" else f"POSITIONAL STOCKS {self.engine.pool.upper()}"
        emoji = "profit" if position.pnl >= 0 else "loss"
        msg = (
            f"[{tag}] SELL {position.symbol} ({emoji})\n\n"
            f"Reason: {position.exit_reason}\n"
            f"Entry: {position.entry_price} -> Exit: {position.exit_price}\n"
            f"PnL: {position.pnl:+.2f} ({position.pnl_pct:+.2f}%)"
        )
        await notifier.send_message(msg)

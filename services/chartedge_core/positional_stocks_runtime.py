"""
positional_stocks_runtime.py
-----------------------------
Live wiring for positional_stocks.py against the running market runtime.
Runs genuinely once per calendar day, after a post-close cutoff (default
15:35 IST) -- daily-swing technical investment, not intraday trading.
Reads runtime.candles[symbol] read-only (1-minute bars), resamples to
daily OHLC itself, computes confluence, and calls the engine's BUY-only
maybe_enter() / check_exit(). Sends Telegram alerts tagged
"[POSITIONAL STOCKS]" via the same global notifier used elsewhere,
distinct from the options positional module's "[POSITIONAL]" tag.
"""
from __future__ import annotations

from datetime import datetime, date, time as dtime
from zoneinfo import ZoneInfo

from services.chartedge_core.positional_stocks import (
    PositionalStocksEngine, daily_candles_from_1m, compute_stock_signal,
)

IST = ZoneInfo("Asia/Kolkata")


class PositionalStocksRuntime:
    def __init__(self, engine: PositionalStocksEngine, config: dict):
        self.engine = engine
        self.config = config
        self._last_check_date: date | None = None

    async def check_once_per_day(self, market_runtime) -> None:
        now = datetime.now(IST)
        today = now.date()

        if self._last_check_date == today:
            return
        cutoff = self.config.get("check_after_time", "15:35")
        cutoff_h, cutoff_m = (int(x) for x in cutoff.split(":"))
        if now.time() < dtime(cutoff_h, cutoff_m):
            return

        symbols: list[str] = self.config.get("symbols", [])
        buy_threshold = self.config.get("buy_threshold", 0.50)
        sell_threshold = self.config.get("sell_threshold", -0.50)
        min_adx = self.config.get("min_adx", 20.0)
        weights = self.config.get("indicator_weights", {})

        for symbol in symbols:
            candles_1m = market_runtime.candles.get(symbol, [])
            if not candles_1m:
                continue
            daily = daily_candles_from_1m(candles_1m)
            if len(daily) < 20:
                continue  # not enough daily history yet for any indicator to be meaningful

            score, indicators = compute_stock_signal(daily, weights.get(symbol, {}))
            price = daily[-1].close
            adx_value = indicators["adx"].value

            if symbol in self.engine.open_positions:
                closed = self.engine.check_exit(symbol, today, price, score, sell_threshold)
                if closed:
                    await self._notify_exit(closed)
            else:
                opened = self.engine.maybe_enter(symbol, today, price, score, buy_threshold, adx_value, min_adx)
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

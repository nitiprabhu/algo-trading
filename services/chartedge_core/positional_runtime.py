"""
positional_runtime.py
----------------------
Live wiring for positional_trading.py against a pluggable market-data
provider (see positional_data_provider.py -- IndstocksDataProvider or
UpstoxDataProvider, selected via shared/config.yaml positional_risk.
data_source). Runs one check per trading day (idempotent -- safe to call
every few minutes). Sends Telegram alerts on entry/exit. Fully separate
from the intraday PaperTradingEngine/FuturesTradingEngine -- reads
market data read-only, never touches intraday positions or capital.

`market_runtime` is still accepted by check_once_per_day() but is now
used ONLY for _trend_pct() (intraday-candle-based; stays INDstocks-only
regardless of data_source -- out of scope for the Upstox swap). All
spot/VIX/option-chain/premium resolution goes through self.provider.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional
from zoneinfo import ZoneInfo

from services.chartedge_core.positional_trading import PositionalTradingEngine
from services.chartedge_core.positional_data_provider import MarketDataProvider

IST = ZoneInfo("Asia/Kolkata")


class PositionalRuntime:
    def __init__(self, engine: PositionalTradingEngine, config: dict, provider: MarketDataProvider):
        self.engine = engine
        self.config = config
        self.provider = provider
        self._last_check_date: Optional[date] = None

    async def check_once_per_day(self, market_runtime=None, force: bool = False) -> None:
        now = datetime.now(IST)
        today = now.date()
        # only run the check once fully resolved per day, and only during market hours
        # (force=True bypasses this, for manual/external triggers -- see
        # /api/positional/trigger, same semantics as positional_stocks_runtime's force=True)
        if not force and (now.time().hour < 9 or now.time().hour >= 16):
            return

        spot = await self.provider.get_spot("NIFTY")
        if spot is None:
            return
        vix = await self.provider.get_vix()

        if self.engine.open_trade is not None:
            legs = self.engine.open_trade.legs
            chain = await self.provider.get_option_chain(spot, "NIFTY", range_strikes=15, current_dt=now)
            premiums = await self.provider.get_leg_premiums(chain, legs)
            if premiums:
                trade = self.engine.mark_to_market(today, premiums)
                if trade:
                    await self._notify_exit(trade)
        else:
            chain = await self.provider.get_option_chain(
                spot, "NIFTY", range_strikes=15, current_dt=now,
                expiry_buffer_days=self.config.get("expiry_buffer_days", 1),
            )
            if not chain:
                return
            expiries_seen = sorted({row.get("expiry") for row in chain if row.get("expiry")})
            target_expiry = None
            if expiries_seen:
                try:
                    target_expiry = datetime.strptime(expiries_seen[0], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    target_expiry = self.engine.strategy.next_expiry(today)
            trend_pct = self._trend_pct(market_runtime) if market_runtime is not None else 0.0
            legs_needed = self._legs_for(spot, vix, today, target_expiry, trend_pct)
            premiums = await self.provider.get_leg_premiums(chain, legs_needed)
            if premiums:
                trade = self.engine.maybe_enter(today, spot, vix, premiums, target_expiry=target_expiry, trend_pct=trend_pct)
                if trade:
                    await self._notify_entry(trade)

        self._last_check_date = today

    def _legs_for(self, spot, vix, today, target_expiry, trend_pct):
        dte = max((target_expiry - today).days, 1) if target_expiry else 6
        return self.engine.strategy.size_legs(spot, vix, dte, trend_pct)

    def _trend_pct(self, market_runtime, lookback: int = 5) -> float:
        candles = market_runtime.candles.get("NIFTY", [])
        if len(candles) <= lookback:
            return 0.0
        old, new = candles[-lookback - 1].close, candles[-1].close
        return (new - old) / old * 100.0 if old else 0.0

    async def _notify_entry(self, trade) -> None:
        from services.chartedge_core.telegram import notifier
        legs_str = " | ".join(f"{leg.side[0]}{leg.option_type}{leg.strike:.0f}" for leg in trade.legs)
        msg = (
            f"🦅 *[POSITIONAL] Weekly {trade.strategy.title()} ENTERED*\n\n"
            f"📅 Entry: `{trade.entry_date}` → Expiry: `{trade.expiry}`\n"
            f"📊 Spot: `{trade.spot_at_entry:.2f}` | VIX: `{trade.vix_at_entry:.2f}`\n"
            f"🎯 Legs: {legs_str}\n"
            f"💰 Credit: `₹{trade.credit:.2f}` x {trade.quantity}"
        )
        await notifier.send_message(msg)

    async def _notify_exit(self, trade) -> None:
        from services.chartedge_core.telegram import notifier
        emoji = "✅" if trade.pnl >= 0 else "❌"
        msg = (
            f"{emoji} *[POSITIONAL] Weekly {trade.strategy.title()} CLOSED*\n\n"
            f"🔚 Reason: `{trade.exit_reason}`\n"
            f"💰 Credit: `₹{trade.credit:.2f}` → Debit: `₹{trade.debit:.2f}`\n"
            f"📈 PnL: `₹{trade.pnl:+.2f}`"
        )
        await notifier.send_message(msg)

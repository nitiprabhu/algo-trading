"""
positional_runtime.py
----------------------
Live wiring for positional_trading.py against the Upstox market-data
provider (see positional_data_provider.py -- UpstoxDataProvider).
Runs one check per trading day (idempotent -- safe to call
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
                open_legs = self.engine.open_trade.legs
                open_qty = self.engine.open_trade.quantity
                trade = self.engine.mark_to_market(today, premiums)
                if trade:
                    live_note = self._execute_live(open_legs, open_qty, chain, entry=False)
                    await self._notify_exit(trade, live_note)
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
                    live_note = self._execute_live(trade.legs, trade.quantity, chain, entry=True)
                    await self._notify_entry(trade, live_note)

        self._last_check_date = today

    def _execute_live(self, legs, quantity: int, chain: list[dict], entry: bool) -> str:
        """Mirror the paper decision as a real Upstox basket order, gated by
        positional_risk.live_trading (enabled+dry_run, common-pool margin
        check -- see upstox_options_broker.py). Paper record is the source
        of truth either way; this returns a one-line status for the Telegram
        alert instead of raising. Instrument keys come from the option-chain
        rows already fetched this cycle (Upstox pre-annotates ce/pe tokens)."""
        live_cfg = self.config.get("live_trading") or {}
        if not live_cfg.get("enabled", False):
            return ""
        try:
            from services.chartedge_core.upstox_options_broker import LegOrder, options_broker
            key_by_strike: dict[float, dict[str, str]] = {}
            for row in chain:
                key_by_strike[row.get("strike")] = {
                    "CE": row.get("ce_token", ""), "PE": row.get("pe_token", ""),
                }
            leg_orders = []
            for leg in legs:
                ikey = key_by_strike.get(leg.strike, {}).get(leg.option_type, "")
                if not ikey:
                    return (f"⚠️ live skipped: no instrument key for "
                            f"{leg.option_type} {leg.strike:.0f} in chain")
                leg_orders.append(LegOrder(
                    instrument_key=ikey,
                    # entry: SHORT leg = SELL, LONG leg = BUY; exit reverses via close_basket
                    transaction_type="SELL" if leg.side == "SHORT" else "BUY",
                    quantity=quantity,
                    label=f"{leg.side} {leg.option_type} {leg.strike:.0f}",
                ))
            broker = options_broker(live_cfg)
            tag = "POS_CONDOR" if entry else "POS_CONDOR-EXIT"
            result = broker.place_basket(leg_orders, tag) if entry else broker.close_basket(leg_orders, tag)
            prefix = "🧪 DRY" if result.simulated else ("🟢 LIVE" if result.ok else "🔴 LIVE FAILED")
            print(f"[Positional/{'ENTRY' if entry else 'EXIT'}] {prefix}: {result.summary()}")
            return f"{prefix}: {result.summary()}"
        except Exception as e:
            print(f"⚠️ [Positional] live execution error: {e}")
            return f"🔴 live execution error: {e}"

    def _legs_for(self, spot, vix, today, target_expiry, trend_pct):
        dte = max((target_expiry - today).days, 1) if target_expiry else 6
        return self.engine.strategy.size_legs(spot, vix, dte, trend_pct)

    def _trend_pct(self, market_runtime, lookback: int = 5) -> float:
        candles = market_runtime.candles.get("NIFTY", [])
        if len(candles) <= lookback:
            return 0.0
        old, new = candles[-lookback - 1].close, candles[-1].close
        return (new - old) / old * 100.0 if old else 0.0

    async def _notify_entry(self, trade, live_note: str = "") -> None:
        from services.chartedge_core.telegram import notifier
        legs_str = " | ".join(f"{leg.side[0]}{leg.option_type}{leg.strike:.0f}" for leg in trade.legs)
        msg = (
            f"🦅 *[POSITIONAL] Weekly {trade.strategy.title()} ENTERED*\n\n"
            f"📅 Entry: `{trade.entry_date}` → Expiry: `{trade.expiry}`\n"
            f"📊 Spot: `{trade.spot_at_entry:.2f}` | VIX: `{trade.vix_at_entry:.2f}`\n"
            f"🎯 Legs: {legs_str}\n"
            f"💰 Credit: `₹{trade.credit:.2f}` x {trade.quantity}"
        )
        if live_note:
            msg += f"\n🏦 Broker: {live_note}"
        await notifier.send_message(msg)

    async def _notify_exit(self, trade, live_note: str = "") -> None:
        from services.chartedge_core.telegram import notifier
        emoji = "✅" if trade.pnl >= 0 else "❌"
        msg = (
            f"{emoji} *[POSITIONAL] Weekly {trade.strategy.title()} CLOSED*\n\n"
            f"🔚 Reason: `{trade.exit_reason}`\n"
            f"💰 Credit: `₹{trade.credit:.2f}` → Debit: `₹{trade.debit:.2f}`\n"
            f"📈 PnL: `₹{trade.pnl:+.2f}`"
        )
        if live_note:
            msg += f"\n🏦 Broker: {live_note}"
        await notifier.send_message(msg)

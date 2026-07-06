"""
positional_runtime.py
----------------------
Live wiring for positional_trading.py against the running IndstocksMarketRuntime.
Runs one check per trading day (idempotent -- safe to call every few minutes).
Sends Telegram alerts on entry/exit. Fully separate from the intraday
PaperTradingEngine/FuturesTradingEngine -- reads runtime.candles/runtime.dm
read-only, never touches intraday positions or capital.
"""
from __future__ import annotations

import os
import httpx
from datetime import datetime, date
from typing import Optional
from zoneinfo import ZoneInfo

from services.chartedge_core.positional_trading import PositionalTradingEngine

IST = ZoneInfo("Asia/Kolkata")


def _fetch_option_ltp(base_url: str, token: str, scrip_code: str) -> Optional[float]:
    """One-off REST quote via the last 1-min historical candle (INDstocks has no
    dedicated LTP endpoint here; reuses the same historical API the intraday
    engine already relies on)."""
    try:
        now = datetime.now(IST)
        start = now.replace(hour=9, minute=15, second=0, microsecond=0)
        resp = httpx.get(
            f"{base_url}/market/historical/1minute",
            headers={"Authorization": token},
            params={
                "scrip-codes": scrip_code,
                "start_time": int(start.timestamp() * 1000),
                "end_time": int(now.timestamp() * 1000),
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        rows = resp.json().get("data", []) or resp.json().get(scrip_code, [])
        if not rows:
            return None
        return float(rows[-1].get("close") or rows[-1][4])
    except Exception as e:
        print(f"⚠️ [Positional] LTP fetch failed for {scrip_code}: {e}")
        return None


class PositionalRuntime:
    def __init__(self, engine: PositionalTradingEngine, config: dict):
        self.engine = engine
        self.config = config
        self._last_check_date: Optional[date] = None

    async def check_once_per_day(self, market_runtime) -> None:
        now = datetime.now(IST)
        today = now.date()
        # only run the check once fully resolved per day, and only during market hours
        if now.time().hour < 9 or now.time().hour >= 16:
            return

        try:
            spot = market_runtime.candles["NIFTY"][-1].close
        except (KeyError, IndexError):
            return
        try:
            vix = market_runtime.candles["INDIAVIX"][-1].close
        except (KeyError, IndexError):
            vix = 15.0

        dm = getattr(market_runtime, "dm", None)
        if dm is None:
            return

        base_url = market_runtime.indstocks["base_url"]
        token = os.getenv("INDMONEY_TOKEN", "")

        if self.engine.open_trade is not None:
            legs = self.engine.open_trade.legs
            chain = dm.get_option_chain(spot, "NIFTY", range_strikes=15, current_dt=now)
            premiums = self._resolve_premiums(chain, legs, base_url, token)
            if premiums:
                trade = self.engine.mark_to_market(today, premiums)
                if trade:
                    await self._notify_exit(trade)
        else:
            chain = dm.get_option_chain(spot, "NIFTY", range_strikes=15, current_dt=now,
                                         expiry_buffer_days=self.config.get("expiry_buffer_days", 1))
            if not chain:
                return
            expiries_seen = sorted({row.get("expiry") for row in chain if row.get("expiry")})
            target_expiry = None
            if expiries_seen:
                try:
                    target_expiry = datetime.strptime(expiries_seen[0], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    target_expiry = self.engine.strategy.next_expiry(today)
            trend_pct = self._trend_pct(market_runtime)
            legs_needed = self._legs_for(spot, vix, today, target_expiry, trend_pct)
            premiums = self._resolve_premiums(chain, legs_needed, base_url, token)
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

    def _resolve_premiums(self, chain: list[dict], legs, base_url: str, token: str) -> dict:
        strike_set = {leg.strike for leg in legs}
        premiums = {}
        for row in chain:
            strike = row.get("strike")
            if strike not in strike_set:
                continue
            entry = {}
            ce_token = row.get("ce_token", "").split(":")[-1]
            pe_token = row.get("pe_token", "").split(":")[-1]
            if ce_token:
                ltp = _fetch_option_ltp(base_url, token, ce_token)
                if ltp is not None:
                    entry["CE"] = ltp
            if pe_token:
                ltp = _fetch_option_ltp(base_url, token, pe_token)
                if ltp is not None:
                    entry["PE"] = ltp
            if entry:
                premiums[strike] = entry
        return premiums

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

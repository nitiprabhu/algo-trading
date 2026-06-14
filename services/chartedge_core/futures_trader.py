"""
futures_trader.py
-----------------
Standalone execution engine for Nifty Futures intraday trading.

Key differences from the options PaperTradingEngine:
  - Lot size = 75 (NSE mandated, updated Nov 2024)
  - MTM = (current_price - entry_price) * lot_size * direction  (linear, no BSM)
  - SL / Targets are in index-point space, not premium-% space
  - No theta gate, no options confidence buffer, no expiry logic
  - Trailing SL via Supertrend value (in index space — consistent with price)
  - Hard EOD exit at 15:10 IST
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time
from typing import Optional
from uuid import uuid4

from services.chartedge_core.models import Candle, Direction, PaperTrade, PositionStatus, Signal
from services.chartedge_core.database import persist_trade_entry, persist_trade_exit
from services.chartedge_core.costs import futures_entry_cost, futures_round_trip_cost


# ── Constants ──────────────────────────────────────────────────────────────────
NIFTY_FUT_LOT = 75
EOD_EXIT_TIME = time(15, 10)
ENTRY_CUTOFF   = time(14, 30)   # no new futures entries after 14:30


class FuturesTrade:
    """Lightweight trade record for a single Nifty Futures lot."""

    def __init__(
        self,
        signal: Signal,
        entry_price: float,
        entry_time: datetime,
        sl_price: float,
        t1_price: float,
        t2_price: float,
        lot_size: int = NIFTY_FUT_LOT,
        max_lots: int = 1,
    ):
        self.id          = uuid4()
        self.signal_id   = signal.id
        self.instrument  = signal.instrument           # e.g. "NIFTY_FUT"
        self.direction   = signal.signal               # Direction.BUY / Direction.SELL
        self.entry_price = entry_price
        self.entry_time  = entry_time
        self.lot_size    = lot_size
        self.quantity    = lot_size * max_lots
        self.sl_price    = sl_price
        self.t1_price    = t1_price
        self.t2_price    = t2_price
        self.t1_hit      = False
        self.pnl         = 0.0
        self.pnl_pct     = 0.0
        self.highest_pnl_pct = 0.0
        self.exit_price: Optional[float] = None
        self.exit_time:  Optional[datetime] = None
        self.exit_reason: Optional[str] = None
        self.status      = PositionStatus.OPEN
        self.invested_amount = entry_price * self.quantity

    def to_paper_trade(self) -> PaperTrade:
        """Convert to PaperTrade for DB persistence and dashboard serialisation."""
        return PaperTrade(
            id=self.id,
            signal_id=self.signal_id,
            instrument=self.instrument,
            direction=self.direction,
            entry_price=self.entry_price,
            entry_time=self.entry_time,
            quantity=self.quantity,
            sl_price=self.sl_price,
            t1_price=self.t1_price,
            t2_price=self.t2_price,
            status=self.status,
            pnl=self.pnl,
            pnl_pct=self.pnl_pct,
            invested_amount=self.invested_amount,
            t1_hit=self.t1_hit,
            highest_pnl_pct=self.highest_pnl_pct,
            exit_price=self.exit_price,
            exit_time=self.exit_time,
            exit_reason=self.exit_reason,
        )


class FuturesTradingEngine:
    """
    Manages paper/simulated Nifty Futures positions.

    This engine is intentionally separate from PaperTradingEngine to avoid
    polluting options logic with futures-specific concerns (lot size, linear MTM,
    index-point SL, etc.).
    """

    def __init__(self, futures_risk_cfg: dict, is_backtesting: bool = False):
        cfg = futures_risk_cfg.get("NIFTY_FUT", {})
        self.lot_size    = int(cfg.get("lot_size", NIFTY_FUT_LOT))
        self.max_lots    = int(cfg.get("max_lots", 1))
        self.sl_points   = float(cfg.get("sl_points", 50))
        self.t1_points   = float(cfg.get("target_1_points", 75))
        self.t2_points   = float(cfg.get("target_2_points", 150))
        self.is_backtesting = is_backtesting

        self.open_positions: dict[str, FuturesTrade] = {}   # instrument → trade
        self.closed_trades:  list[FuturesTrade]      = []
        self.kill_switch = False
        self.apply_costs = True

    def _resolve_levels(
        self, signal: Signal, direction: Direction, entry_price: float
    ) -> tuple[float, float, float]:
        """Use strategy SL/T1/T2 when valid; otherwise fall back to fixed point offsets."""
        use_signal = signal.strategy_name in ("FUT_ORB", "FUT_ORB_SESSION", "FUT_MID", "FUT_CLOSE") or (
            signal.strategy_name or ""
        ).startswith("FUT_")

        if use_signal and signal.stop_loss:
            sl = round(signal.stop_loss, 2)
            t1 = round(signal.target_1, 2)
            t2 = round(signal.target_2, 2)
            if direction == Direction.BUY and sl < entry_price < t1 <= t2:
                return sl, t1, t2
            if direction == Direction.SELL and sl > entry_price > t1 >= t2:
                return sl, t1, t2

        if direction == Direction.BUY:
            return (
                round(entry_price - self.sl_points, 2),
                round(entry_price + self.t1_points, 2),
                round(entry_price + self.t2_points, 2),
            )
        return (
            round(entry_price + self.sl_points, 2),
            round(entry_price - self.t1_points, 2),
            round(entry_price - self.t2_points, 2),
        )

    # ─────────────────────────── Entry ────────────────────────────────────────

    async def maybe_enter(self, signal: Signal, candle: Candle) -> Optional[FuturesTrade]:
        """Attempt to open a new futures position."""
        t = candle.time.time()

        # Gates
        if self.kill_switch:
            return None
        if t < time(9, 15) or t >= ENTRY_CUTOFF:
            return None
        if signal.signal == Direction.HOLD:
            return None
        if signal.instrument in self.open_positions:
            print(f"⏳ [Futures] Already in {signal.instrument} — skipping duplicate entry")
            return None

        direction   = signal.signal
        entry_price = round(candle.open * (1.0005 if direction == Direction.BUY else 0.9995), 2)
        sl_price, t1_price, t2_price = self._resolve_levels(signal, direction, entry_price)

        entry_costs = (
            futures_entry_cost(entry_price, self.lot_size * self.max_lots, direction.value).total
            if self.apply_costs else 0.0
        )

        trade = FuturesTrade(
            signal=signal,
            entry_price=entry_price,
            entry_time=candle.time,
            sl_price=sl_price,
            t1_price=t1_price,
            t2_price=t2_price,
            lot_size=self.lot_size,
            max_lots=self.max_lots,
        )

        self.open_positions[signal.instrument] = trade
        cost_suffix = f" Costs=₹{entry_costs:.2f}" if self.apply_costs else ""
        print(
            f"🚀 [FUTURES ENTERED] {signal.instrument} {direction.value} @ {entry_price} | "
            f"SL={sl_price} T1={t1_price} T2={t2_price}{cost_suffix} ({candle.time})"
        )

        if not self.is_backtesting:
            persist_trade_entry(trade.to_paper_trade())
            await self._send_telegram_entry(trade)

        return trade

    # ─────────────────────────── MTM / Exit ───────────────────────────────────

    async def mark_to_market(
        self,
        candle: Candle,
        supertrend_value: Optional[float] = None,
    ) -> None:
        """Update all open positions and check exits."""
        for instrument, trade in list(self.open_positions.items()):
            # Price discovery: futures price tracks spot closely (basis ≈ 5–15 pts)
            # In live, we'd get the actual futures LTP. In backtest, spot ≈ futures.
            current_price = candle.close

            # Linear MTM (the key difference from options)
            direction_mult = 1 if trade.direction == Direction.BUY else -1
            trade.pnl     = round((current_price - trade.entry_price) * direction_mult * trade.quantity, 2)
            trade.pnl_pct = round(trade.pnl / trade.invested_amount * 100, 2)
            trade.highest_pnl_pct = max(trade.highest_pnl_pct, trade.pnl_pct)

            t_candle = candle.time
            t_entry  = trade.entry_time
            # Make timezone-aware comparison safe
            if t_candle.tzinfo is not None and t_entry.tzinfo is None:
                t_candle = t_candle.replace(tzinfo=None)
            elif t_candle.tzinfo is None and t_entry.tzinfo is not None:
                t_entry = t_entry.replace(tzinfo=None)
            if t_candle < t_entry:
                continue

            # ── 1. EOD Hard Exit ───────────────────────────────────────────
            if candle.time.time() >= EOD_EXIT_TIME:
                await self._close(trade, current_price, candle.time, "EOD_SQUAREOFF")
                continue

            # ── 2. Stop Loss ───────────────────────────────────────────────
            if trade.direction == Direction.BUY and current_price <= trade.sl_price:
                await self._close(trade, trade.sl_price, candle.time, "SL")
                continue
            if trade.direction == Direction.SELL and current_price >= trade.sl_price:
                await self._close(trade, trade.sl_price, candle.time, "SL")
                continue

            # ── 3. Target 2 (Full Exit) ────────────────────────────────────
            if trade.direction == Direction.BUY and current_price >= trade.t2_price:
                await self._close(trade, trade.t2_price, candle.time, "T2")
                continue
            if trade.direction == Direction.SELL and current_price <= trade.t2_price:
                await self._close(trade, trade.t2_price, candle.time, "T2")
                continue

            # ── 4. Supertrend Trailing SL (index-point space — safe for futures) ──
            if supertrend_value is not None and isinstance(supertrend_value, (int, float)):
                if trade.direction == Direction.BUY and supertrend_value > trade.sl_price:
                    print(f"📈 [Futures] Supertrend trail SL: {trade.sl_price} → {supertrend_value:.2f}")
                    trade.sl_price = round(supertrend_value, 2)
                elif trade.direction == Direction.SELL and supertrend_value < trade.sl_price:
                    print(f"📉 [Futures] Supertrend trail SL: {trade.sl_price} → {supertrend_value:.2f}")
                    trade.sl_price = round(supertrend_value, 2)

            # ── 5. Target 1 → Move SL to Breakeven ────────────────────────
            if not trade.t1_hit:
                if trade.direction == Direction.BUY and current_price >= trade.t1_price:
                    trade.t1_hit  = True
                    trade.sl_price = trade.entry_price   # lock breakeven
                    print(f"🎯 [Futures] T1 hit — SL moved to breakeven {trade.entry_price}")
                elif trade.direction == Direction.SELL and current_price <= trade.t1_price:
                    trade.t1_hit  = True
                    trade.sl_price = trade.entry_price
                    print(f"🎯 [Futures] T1 hit — SL moved to breakeven {trade.entry_price}")

    # ─────────────────────────── Close helper ─────────────────────────────────

    async def _close(self, trade: FuturesTrade, price: float, at: datetime, reason: str) -> None:
        slippage = 0.9995 if trade.direction == Direction.BUY else 1.0005
        actual_price = round(price * slippage, 2)
        direction_mult = 1 if trade.direction == Direction.BUY else -1

        gross_pnl = round((actual_price - trade.entry_price) * direction_mult * trade.quantity, 2)
        costs = (
            futures_round_trip_cost(
                trade.entry_price, actual_price, trade.quantity, trade.direction.value
            )
            if self.apply_costs else 0.0
        )
        trade.pnl        = round(gross_pnl - costs, 2)
        trade.pnl_pct    = round(trade.pnl / trade.invested_amount * 100, 2)
        trade.exit_price  = actual_price
        trade.exit_time   = at
        trade.exit_reason = reason
        trade.status      = PositionStatus.CLOSED

        del self.open_positions[trade.instrument]
        self.closed_trades.append(trade)
        self.closed_trades = self.closed_trades[-200:]

        pnl_emoji = "✅" if trade.pnl >= 0 else "❌"
        cost_suffix = f" Costs=₹{costs:.2f}" if self.apply_costs else ""
        print(
            f"{pnl_emoji} [FUTURES CLOSED] {trade.instrument} {trade.direction.value} | "
            f"Entry={trade.entry_price} Exit={actual_price} PnL=₹{trade.pnl:+.2f}{cost_suffix} ({reason})"
        )

        if not self.is_backtesting:
            persist_trade_exit(
                str(trade.id), actual_price, at, reason, trade.pnl, trade.pnl_pct
            )
            await self._send_telegram_exit(trade)

    async def force_close_all(self, price_map: dict[str, float], now: datetime, reason: str) -> None:
        for trade in list(self.open_positions.values()):
            price = price_map.get(trade.instrument, trade.entry_price)
            await self._close(trade, price, now, reason)

    def reset(self) -> None:
        self.open_positions.clear()
        self.closed_trades.clear()
        self.kill_switch = False

    # ─────────────────────────── Metrics ──────────────────────────────────────

    def metrics(self) -> dict:
        realized = sum(t.pnl for t in self.closed_trades)
        open_pnl = sum(t.pnl for t in self.open_positions.values())
        wins = [t for t in self.closed_trades if t.pnl > 0]
        losses = [t for t in self.closed_trades if t.pnl <= 0]
        win_rate = round(len(wins) / len(self.closed_trades) * 100, 1) if self.closed_trades else 0.0
        return {
            "futures_realized_pnl": realized,
            "futures_open_pnl": open_pnl,
            "futures_total_trades": len(self.closed_trades),
            "futures_win_rate": win_rate,
            "futures_wins": len(wins),
            "futures_losses": len(losses),
        }

    # ─────────────────────────── Notifications ────────────────────────────────

    async def _send_telegram_entry(self, trade: FuturesTrade) -> None:
        try:
            from services.chartedge_core.telegram import notifier
            msg = (
                f"🚀 *FUTURES ENTERED*\n\n"
                f"📊 *Instrument:* `{trade.instrument}`\n"
                f"📈 *Direction:* `{trade.direction.value}`\n"
                f"💰 *Entry:* `₹{trade.entry_price:.2f}`\n"
                f"📦 *Qty:* `{trade.quantity}` ({trade.quantity // trade.lot_size} lot)\n"
                f"🛡️ *SL:* `₹{trade.sl_price:.2f}` ({self.sl_points} pts)\n"
                f"🎯 *T1:* `₹{trade.t1_price:.2f}` | *T2:* `₹{trade.t2_price:.2f}`"
            )
            asyncio.create_task(notifier.send_message(msg))
        except Exception:
            pass

    async def _send_telegram_exit(self, trade: FuturesTrade) -> None:
        try:
            from services.chartedge_core.telegram import notifier
            emoji = "✅" if trade.pnl >= 0 else "❌"
            msg = (
                f"{emoji} *FUTURES CLOSED*\n\n"
                f"📊 *Instrument:* `{trade.instrument}`\n"
                f"📈 *Direction:* `{trade.direction.value}`\n"
                f"💰 *Entry:* `₹{trade.entry_price:.2f}` → *Exit:* `₹{trade.exit_price:.2f}`\n"
                f"📦 *Qty:* `{trade.quantity}`\n"
                f"💵 *PnL:* `₹{trade.pnl:+.2f}` ({trade.pnl_pct:+.2f}%)\n"
                f"🔚 *Reason:* `{trade.exit_reason}`"
            )
            asyncio.create_task(notifier.send_message(msg))
        except Exception:
            pass

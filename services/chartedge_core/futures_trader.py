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

import asyncio
from datetime import datetime, timedelta, time
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

    def __init__(
        self,
        futures_risk_cfg: dict,
        is_backtesting: bool = False,
        risk_config: dict | None = None,
    ):
        cfg = futures_risk_cfg.get("NIFTY_FUT", {})
        self.lot_size    = int(cfg.get("lot_size", NIFTY_FUT_LOT))
        self.max_lots    = int(cfg.get("max_lots", 1))
        self.sl_points   = float(cfg.get("sl_points", 50))
        self.t1_points   = float(cfg.get("target_1_points", 75))
        self.t2_points   = float(cfg.get("target_2_points", 150))
        self.is_backtesting = is_backtesting
        self.risk_config = risk_config or {}

        self.open_positions: dict[str, FuturesTrade] = {}   # instrument → trade
        self.closed_trades:  list[FuturesTrade]      = []
        self.kill_switch = False
        self.consecutive_losses = 0
        self.cooldown_until: datetime | None = None
        self.apply_costs = True
        # True right after boot while stale/prior-day trades are recovered from DB —
        # suppresses per-trade Telegram alerts until finish_startup_backfill() is called.
        self.is_startup_backfill: bool = not is_backtesting

        if not is_backtesting:
            self.load_active_trades()

    def finish_startup_backfill(self) -> None:
        """Call once boot recovery + startup summary are done to resume live per-trade alerts."""
        self.is_startup_backfill = False

    def load_active_trades(self) -> None:
        """Load open futures trades from the database to resume tracking after a restart."""
        from services.chartedge_core.database import get_open_trades
        from uuid import UUID
        records = get_open_trades()
        for r in records:
            is_fut = "_FUT" in r.symbol or r.symbol.endswith("_FUT")
            if not is_fut:
                continue
                
            from zoneinfo import ZoneInfo
            from datetime import timezone
            IST = ZoneInfo("Asia/Kolkata")
            entry_time = r.entry_time
            if entry_time is not None:
                if entry_time.tzinfo is not None:
                    entry_time = entry_time.astimezone(IST).replace(tzinfo=None)

                
            class DummySignal:
                def __init__(self, sid, inst, d):
                    self.id = sid
                    self.instrument = inst
                    self.signal = d
                    
            dummy_signal = DummySignal(
                UUID(r.signal_id), 
                r.symbol, 
                Direction.BUY if r.direction == "BUY" else Direction.SELL
            )
            
            trade = FuturesTrade(
                signal=dummy_signal,
                entry_price=r.entry_price,
                entry_time=entry_time,
                sl_price=r.sl_price,
                t1_price=r.t1_price,
                t2_price=r.t2_price,
                lot_size=self.lot_size,
                max_lots=self.max_lots
            )
            trade.id = UUID(r.trade_id)
            trade.pnl = r.pnl
            trade.pnl_pct = r.pnl_pct
            trade.t1_hit = r.t1_hit
            trade.highest_pnl_pct = getattr(r, "highest_pnl_pct", 0.0)
            trade.invested_amount = r.invested_amount
            
            self.open_positions[trade.instrument] = trade
            print(f"🔄 Recovered active futures trade: {trade.direction.value} {trade.instrument} from {trade.entry_time}")

    def _resolve_levels(
        self, signal: Signal, direction: Direction, entry_price: float
    ) -> tuple[float, float, float]:
        """Use strategy SL/T1/T2 when valid; otherwise fall back to fixed point offsets."""
        use_signal = signal.strategy_name in ("FUT_ORB", "FUT_ORB_SESSION", "FUT_MID", "FUT_MIDDAY", "FUT_CLOSE") or (
            signal.strategy_name or ""
        ).startswith("FUT_")

        if use_signal and signal.stop_loss:
            sl = round(signal.stop_loss, 2)
            # Enforce sanity cap on stop-loss distance (between 30 and 120 points)
            sl_distance = abs(entry_price - sl)
            if sl_distance < 30.0:
                sl_distance = 30.0
                sl = round(entry_price - sl_distance if direction == Direction.BUY else entry_price + sl_distance, 2)
            elif sl_distance > 120.0:
                sl_distance = 120.0
                sl = round(entry_price - sl_distance if direction == Direction.BUY else entry_price + sl_distance, 2)

            t1 = round(entry_price + sl_distance * 1.5 if direction == Direction.BUY else entry_price - sl_distance * 1.5, 2)
            t2 = round(entry_price + sl_distance * 3.0 if direction == Direction.BUY else entry_price - sl_distance * 3.0, 2)
            return sl, t1, t2

        atr = signal.indicator_snapshot.indicators.get("atr")
        if atr and isinstance(atr.value, float):
            dynamic_sl_dist = min(max(atr.value * 1.2, 30.0), 80.0)
        else:
            dynamic_sl_dist = self.sl_points

        if direction == Direction.BUY:
            return (
                round(entry_price - dynamic_sl_dist, 2),
                round(entry_price + (dynamic_sl_dist * 1.5), 2),
                round(entry_price + (dynamic_sl_dist * 3.0), 2),
            )
        return (
            round(entry_price + dynamic_sl_dist, 2),
            round(entry_price - (dynamic_sl_dist * 1.5), 2),
            round(entry_price - (dynamic_sl_dist * 3.0), 2),
        )

    # ─────────────────────────── Entry ────────────────────────────────────────

    async def maybe_enter(self, signal: Signal, candle: Candle) -> Optional[FuturesTrade]:
        """Attempt to open a new futures position."""
        t = candle.time.time()

        # Gates
        if self.kill_switch:
            return None
        if self.cooldown_until is not None and candle.time < self.cooldown_until:
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

        total_capital = self.risk_config.get("total_capital", 200000.0)

        # Risk-based sizing: strategy SL distance varies 30-120pts (range width/ATR),
        # but lot count was fixed regardless — a 120pt SL at max_lots risked 3x the
        # intended amount (₹18,000 vs the ₹7,500 implied by sl_points config).
        # Size lots so rupee risk stays constant: wider SL -> fewer lots.
        sl_distance = abs(entry_price - sl_price) or self.sl_points
        risk_pct = self.risk_config.get("futures_risk_per_trade_pct", 1.0)
        risk_budget = total_capital * (risk_pct / 100.0)
        lots = max(1, min(self.max_lots, int(risk_budget // (sl_distance * self.lot_size))))
        quantity = self.lot_size * lots

        margin_pct = 0.12 if "BANKNIFTY" in signal.instrument.upper() else 0.11
        required_margin = entry_price * quantity * margin_pct

        # 1. Calculate used options outlay for margin, and check mutual exclusion if enabled
        used_options_outlay = 0.0
        has_active_options = False
        if hasattr(self, "simulator") and self.simulator:
            active_options = self.simulator.trader.open_positions
            used_options_outlay = sum(p.invested_amount for p in active_options.values())
            has_active_options = len(active_options) > 0
            
        if self.risk_config.get("mutual_exclusion", False) and has_active_options:
            print(f"⛔ [Futures Margin Gate] {signal.instrument}: Blocked futures entry because there is an active Options position (Mutual Exclusion)")
            return None

        # 2. Free Margin Check
        used_futures_margin = 0.0
        for fut_trade in self.open_positions.values():
            m_pct = 0.12 if "BANKNIFTY" in fut_trade.instrument.upper() else 0.11
            used_futures_margin += fut_trade.entry_price * fut_trade.quantity * m_pct

        free_margin = total_capital - used_options_outlay - used_futures_margin

        if required_margin > free_margin:
            print(f"⛔ [Futures Margin Gate] {signal.instrument}: Required margin {required_margin:.2f} exceeds free margin {free_margin:.2f} (Total Cap: {total_capital})")
            return None

        entry_costs = (
            futures_entry_cost(entry_price, quantity, direction.value).total
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
            max_lots=lots,
        )

        self.open_positions[signal.instrument] = trade
        cost_suffix = f" Costs=₹{entry_costs:.2f}" if self.apply_costs else ""
        print(
            f"🚀 [FUTURES ENTERED] {signal.instrument} {direction.value} @ {entry_price} | "
            f"SL={sl_price} T1={t1_price} T2={t2_price}{cost_suffix} ({candle.time})"
        )

        if not self.is_backtesting:
            await asyncio.to_thread(persist_trade_entry, trade.to_paper_trade())
            # Log to realtime/trades_YYYY-MM-DD.log
            log_msg = (
                f"🚀 FUTURES ENTERED\n\n"
                f"🌐 Instrument: {trade.instrument}\n"
                f"📈 Direction: {trade.direction.value}\n"
                f"💰 Entry Price: ₹{trade.entry_price:.2f}\n"
                f"📦 Quantity: {trade.quantity} ({trade.quantity // trade.lot_size} lot)\n"
                f"🛡️ Stop Loss: ₹{trade.sl_price:.2f}\n"
                f"🎯 Target 1: ₹{trade.t1_price:.2f}\n"
                f"🎯 Target 2: ₹{trade.t2_price:.2f}\n\n"
                f"🧠 Reason: {signal.reasoning or 'No reason provided.'}"
            )
            from services.chartedge_core.training_logger import log_realtime_trade_action
            log_realtime_trade_action(log_msg)
            await self._send_telegram_entry(trade)

        return trade

    # ─────────────────────────── MTM / Exit ───────────────────────────────────

    async def mark_to_market(
        self,
        candle: Candle,
        supertrend_value: Optional[float] = None,
    ) -> None:
        for instrument, trade in list(self.open_positions.items()):
            t_candle = candle.time
            t_entry  = trade.entry_time
            # Make timezone-aware comparison safe
            if t_candle.tzinfo is not None and t_entry.tzinfo is None:
                t_candle = t_candle.replace(tzinfo=None)
            elif t_candle.tzinfo is None and t_entry.tzinfo is not None:
                t_entry = t_entry.replace(tzinfo=None)
            if t_candle < t_entry:
                trade.pnl = 0.0
                trade.pnl_pct = 0.0
                continue

            # Price discovery: futures price tracks spot closely (basis ≈ 5–15 pts)
            # In live, we'd get the actual futures LTP. In backtest, spot ≈ futures.
            current_price = candle.close

            # Linear MTM (the key difference from options)
            direction_mult = 1 if trade.direction == Direction.BUY else -1
            trade.pnl     = round((current_price - trade.entry_price) * direction_mult * trade.quantity, 2)
            trade.pnl_pct = round(trade.pnl / trade.invested_amount * 100, 2)
            trade.highest_pnl_pct = max(trade.highest_pnl_pct, trade.pnl_pct)

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
                # Time-based acceleration: if held > 60m, trail more aggressively
                held_mins = (candle.time - trade.entry_time).total_seconds() / 60.0
                
                if trade.direction == Direction.BUY and supertrend_value > trade.sl_price:
                    new_sl = supertrend_value
                    if held_mins > 60:
                        # Ratchet minimum: half distance between entry and supertrend
                        midpoint = trade.entry_price + (supertrend_value - trade.entry_price) * 0.5
                        new_sl = max(supertrend_value, midpoint)
                    print(f"\U0001f4c8 [Futures] Supertrend trail SL: {trade.sl_price} → {new_sl:.2f}")
                    trade.sl_price = round(new_sl, 2)
                elif trade.direction == Direction.SELL and supertrend_value < trade.sl_price:
                    new_sl = supertrend_value
                    if held_mins > 60:
                        midpoint = trade.entry_price - (trade.entry_price - supertrend_value) * 0.5
                        new_sl = min(supertrend_value, midpoint)
                    print(f"\U0001f4c9 [Futures] Supertrend trail SL: {trade.sl_price} → {new_sl:.2f}")
                    trade.sl_price = round(new_sl, 2)

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

        # Daily circuit breaker — check combined daily drawdown pause
        total_capital = self.risk_config.get("total_capital", 200000.0)
        dd_limit = self.risk_config.get("daily_drawdown_pause_pct", 2.5)

        current_date = candle.time.date()
        if hasattr(self, "simulator") and self.simulator:
            dd_pct = self.simulator.get_combined_daily_drawdown_pct(current_date)
        else:
            # Fallback to local futures daily drawdown if simulator is not set
            fut_realized = sum(t.pnl for t in self.closed_trades if t.exit_time and t.exit_time.date() == current_date)
            fut_open = sum(t.pnl for t in self.open_positions.values())
            dd_pct = round(((fut_realized + fut_open) / total_capital) * 100, 2)

        if dd_pct <= -abs(dd_limit):
            print(f"🛑 [Futures] CIRCUIT BREAKER: daily drawdown {dd_pct}% (limit: -{dd_limit}%)")
            if hasattr(self, "simulator") and self.simulator:
                await self.simulator.trigger_global_kill_switch(candle.time, candle=candle)
            else:
                self.kill_switch = True
                await self.force_close_all({instrument: candle.close for instrument in self.open_positions}, candle.time, "KILL_SWITCH")

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

        if trade.pnl < 0:
            self.consecutive_losses += 1
            halt_after = self.risk_config.get("daily_halt_after_losses", 4)
            cooldown_after = self.risk_config.get("cooldown_after_losses", 2)
            if self.consecutive_losses >= halt_after:
                print(f"🛑 [Futures] KILL SWITCH: {self.consecutive_losses} consecutive losses")
                self.kill_switch = True
            elif self.consecutive_losses >= cooldown_after:
                self.cooldown_until = at + timedelta(minutes=45)
                print(
                    f"⏸️  [Futures] COOLDOWN: {self.consecutive_losses} consecutive losses "
                    f"— no new entries until {self.cooldown_until.strftime('%H:%M')}"
                )
        else:
            if self.consecutive_losses > 0:
                print(f"✅ [Futures] Consecutive loss streak reset (was {self.consecutive_losses})")
            self.consecutive_losses = 0
            self.cooldown_until = None

        if not self.is_backtesting:
            await asyncio.to_thread(persist_trade_exit, trade.to_paper_trade())
            # Log to realtime/trades_YYYY-MM-DD.log
            emoji = "🟢" if trade.pnl >= 0 else "🔴"
            log_msg = (
                f"🏁 FUTURES CLOSED\n\n"
                f"🌐 Instrument: {trade.instrument}\n"
                f"📉 Exit Price: ₹{trade.exit_price:.2f}\n"
                f"🚪 Exit Reason: {trade.exit_reason}\n"
                f"{emoji} PnL: ₹{trade.pnl:.2f} ({trade.pnl_pct:.2f}%)\n\n"
                f"💵 Invested Amount: ₹{trade.invested_amount:.2f}"
            )
            from services.chartedge_core.training_logger import log_realtime_trade_action
            log_realtime_trade_action(log_msg)
            await self._send_telegram_exit(trade)

    async def force_close_all(self, price_map: dict[str, float], now: datetime, reason: str) -> None:
        for trade in list(self.open_positions.values()):
            price = price_map.get(trade.instrument, trade.entry_price)
            await self._close(trade, price, now, reason)

    def reset_daily_state(self) -> None:
        """Reset per-day risk counters (called at day boundary in simulation)."""
        self.kill_switch = False
        self.consecutive_losses = 0
        self.cooldown_until = None

    def reset(self) -> None:
        self.open_positions.clear()
        self.closed_trades.clear()
        self.reset_daily_state()

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
        if self.is_startup_backfill:
            return
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
        if self.is_startup_backfill or trade.exit_reason == "EOD_SQUAREOFF":
            return
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

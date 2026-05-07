from __future__ import annotations
from uuid import UUID
from datetime import datetime, time
from typing import Any, Optional

from services.chartedge_core.database import (
    persist_trade_entry, persist_trade_exit, update_trade_mtm,
    get_open_trades, get_recent_closed_trades
)
from services.chartedge_core.training_logger import training_logger
from services.chartedge_core.models import Candle, Direction, IndicatorSnapshot, PaperTrade, PositionStatus, Signal
from services.chartedge_core.utils import order_rate_limiter


class PaperTradingEngine:
    def __init__(self, risk_config: dict, skip_db_load: bool = False, is_backtesting: bool = False) -> None:
        self.risk_config = risk_config
        self.open_positions: dict[str, PaperTrade] = {}
        self.closed_trades: list[PaperTrade] = []
        self.queued_signals: list[Signal] = []
        self.kill_switch_enabled = False
        self.is_backtesting = is_backtesting
        if not skip_db_load and not is_backtesting:
            self.load_active_trades()

    def load_active_trades(self) -> None:
        """Load open trades from the database to resume tracking after a restart."""
        records = get_open_trades()
        for r in records:
            entry_time = r.entry_time
            if hasattr(entry_time, "tzinfo") and entry_time.tzinfo is not None:
                entry_time = entry_time.replace(tzinfo=None)
            trade = PaperTrade(
                id=UUID(r.trade_id),
                signal_id=UUID(r.signal_id),
                instrument=r.symbol,
                direction=Direction.BUY if r.direction == "BUY" else Direction.SELL,
                entry_price=r.entry_price,
                entry_time=entry_time,
                quantity=r.quantity,
                sl_price=r.sl_price,
                t1_price=r.t1_price,
                t2_price=r.t2_price,
                status=PositionStatus.OPEN,
                invested_amount=r.invested_amount,
                pnl=r.pnl,
                pnl_pct=r.pnl_pct,
                t1_hit=r.t1_hit,
                underlying_entry_price=r.underlying_entry_price if hasattr(r, "underlying_entry_price") else None,
                highest_pnl_pct=r.highest_pnl_pct if hasattr(r, "highest_pnl_pct") else 0.0
            )
            self.open_positions[trade.instrument] = trade
            print(f"🔄 Recovered active trade: {trade.direction.value} {trade.instrument} from {trade.entry_time}")
            
        # Also load recent closed history
        closed_records = get_recent_closed_trades(limit=50)
        for r in closed_records:
            trade = PaperTrade(
                id=UUID(r.trade_id),
                signal_id=UUID(r.signal_id),
                instrument=r.symbol,
                direction=Direction.BUY if r.direction == "BUY" else Direction.SELL,
                entry_price=r.entry_price,
                entry_time=r.entry_time,
                quantity=r.quantity,
                sl_price=r.sl_price,
                t1_price=r.t1_price,
                t2_price=r.t2_price,
                status=PositionStatus.CLOSED,
                pnl=r.pnl,
                pnl_pct=r.pnl_pct,
                invested_amount=r.invested_amount,
                t1_hit=r.t1_hit,
                highest_pnl_pct=r.highest_pnl_pct if hasattr(r, "highest_pnl_pct") else 0.0,
                exit_price=r.exit_price,
                exit_time=r.exit_time,
                exit_reason=r.exit_reason
            )
            self.closed_trades.append(trade)
        
        # Sort history by time descending (UI usually expects this)
        self.closed_trades.sort(key=lambda t: t.exit_time if t.exit_time else t.entry_time, reverse=True)
        print(f"📜 Loaded {len(self.closed_trades)} historical trades from database")

    async def maybe_enter(self, signal: Signal, next_candle: Candle, underlying_entry_price: Optional[float] = None) -> PaperTrade | None:
        # Time Filter: No entries before 09:30 or at/after 15:00 (square-off time)
        t = next_candle.time.time()
        if t < time(9, 30) or t >= time(15, 0):
            return None
            
        if self.kill_switch_enabled or signal.signal == Direction.HOLD:
            return None
        if signal.confidence < self.risk_config["confidence_floor"]:
            return None
        if signal.instrument in self.open_positions:
            self.queued_signals.append(signal)
            return None
        if len(self.open_positions) >= self.risk_config["max_open_positions"]:
            self.queued_signals.append(signal)
            return None

        # Apply 0.05% slippage on entry
        slippage_factor = 1.0005 if signal.signal == Direction.BUY else 0.9995
        entry_price = round(next_candle.open * slippage_factor, 2)
        
        # PRD: Capital Allocation (2% Risk Limit of Total Equity)
        total_equity = self.risk_config.get("total_capital", 100000.0) 
        risk_per_trade = total_equity * 0.02 # 2% risk (2000 INR for 1L)
        
        # Risk per share calculation: Floor at 0.5% of entry to prevent infinite quantity on tight SL
        risk_per_share = max(abs(entry_price - signal.stop_loss), entry_price * 0.005)
        
        # Determine lot size
        inst_upper = signal.instrument.upper()
        if "BANKNIFTY" in inst_upper:
            lot_size = 15
        elif "NIFTY" in inst_upper:
            lot_size = 25
        else:
            lot_size = 1
        
        # For Options (Symbols containing _CE or _PE), we also limit based on premium outlay
        # Senior Trader Rule: Max 15% of capital in a single options premium outlay
        max_outlay = total_equity * 0.15 
        
        raw_quantity = risk_per_trade / risk_per_share
        lots_by_risk = int(raw_quantity / lot_size)
        lots_by_outlay = int(max_outlay / (entry_price * lot_size))
        
        # Take the more conservative approach
        lots = max(1, min(lots_by_risk, lots_by_outlay))
        print(f"DEBUG: Trade Calculation for {signal.instrument}: risk_per_share={risk_per_share:.2f}, lots_by_risk={lots_by_risk}, lots_by_outlay={lots_by_outlay}, final_lots={lots}, lot_size={lot_size}")
        
        # Ensure minimum quantity of 50 for Nifty as requested (if funds allow)
        if "NIFTY" in signal.instrument and lots < 2 and max_outlay >= (entry_price * 50):
             lots = 2
             
        quantity = lots * lot_size
        invested_amount = round(entry_price * quantity, 2)
        
        # Final safety check against hard capital limit
        if invested_amount > total_equity * 0.3: # Max 30% capital in one trade (even if risk is low)
            print(f"⚠️ Trade rejected: Invested amount ({invested_amount}) exceeds 30% capital buffer")
            return None
        # Options SL/T1/T2 must be in option-premium domain, not underlying domain.
        # AI/rule-based often returns levels in underlying index terms (e.g., SL=23900 for NIFTY).
        # Detect by: SL is wildly out of range vs entry_price (option premium).
        is_option = any(x in signal.instrument for x in ("-CE", "-PE", "_CE", "_PE"))
        sl_price = signal.stop_loss
        t1_price = signal.target_1
        t2_price = signal.target_2
        if is_option and (sl_price > entry_price * 3 or sl_price < 0):
            # SL is in underlying domain — replace with premium-based levels
            sl_price = round(entry_price * 0.65, 2)   # 35% max loss on premium
            t1_price = round(entry_price * 1.50, 2)   # 50% gain
            t2_price = round(entry_price * 2.00, 2)   # 100% gain (double)
            print(f"⚠️ Options SL domain fix applied for {signal.instrument}: "
                  f"SL={sl_price} T1={t1_price} T2={t2_price} (entry={entry_price})")

        trade = PaperTrade(
            signal_id=signal.id,
            instrument=signal.instrument,
            direction=signal.signal,
            entry_price=entry_price,
            entry_time=next_candle.time,
            quantity=quantity,
            underlying_entry_price=underlying_entry_price,
            invested_amount=invested_amount,
            sl_price=sl_price,
            t1_price=t1_price,
            t2_price=t2_price,
        )
        # Consume rate limit token before "placing" order
        await order_rate_limiter.consume()
        
        print(f"🚀 ENTERED: {signal.instrument} {signal.signal} at {entry_price} ({next_candle.time})")
        trade_instrument = signal.instrument
        self.open_positions[trade_instrument] = trade
        if not self.is_backtesting:
            persist_trade_entry(trade)
        training_logger.log_entry(trade, signal)
        return trade

    async def mark_to_market(self, candle: Candle, snapshot: IndicatorSnapshot | None = None, ltp_map: dict[str, float] | None = None) -> None:
        """Update all open positions. If ltp_map is provided (from live ticks), it takes priority."""
        for symbol, trade in list(self.open_positions.items()):
            current_price = None
            
            # Priority 1: Direct LTP from map (works for Options)
            if ltp_map and symbol in ltp_map:
                current_price = ltp_map[symbol]
            # Priority 2: Current candle if symbols match
            elif symbol == candle.instrument:
                current_price = candle.close
            
            if current_price is None and trade.underlying_entry_price:
                # Correctly identify underlying index
                underlying = "NIFTY" if trade.instrument.startswith("NIFTY") else "BANKNIFTY"
                underlying_price = None
                if ltp_map and underlying in ltp_map:
                    underlying_price = ltp_map[underlying]
                elif underlying == candle.instrument:
                    underlying_price = candle.close
                
                if underlying_price and trade.underlying_entry_price:
                    move = underlying_price - trade.underlying_entry_price
                    delta = 0.5
                    direction_mult = 1 if "_CE" in trade.instrument else -1
                    current_price = round(trade.entry_price + (move * delta * direction_mult), 2)
                    current_price = max(0.01, current_price)
                else:
                    # Fallback to LTP of the instrument itself if available
                    if candle.instrument == trade.instrument:
                        current_price = candle.close
                    elif ltp_map and trade.instrument in ltp_map:
                        current_price = ltp_map[trade.instrument]

            if current_price is None:
                continue

            trade.pnl = self._pnl(trade, current_price)
            trade.pnl_pct = round((trade.pnl / (trade.entry_price * trade.quantity)) * 100, 2) if trade.quantity > 0 and trade.entry_price > 0 else 0.0

            if hasattr(trade, "last_db_update") and trade.last_db_update is not None:
                if not self.is_backtesting and (datetime.now() - trade.last_db_update).seconds > 60:
                    update_trade_mtm(
                        str(trade.id), 
                        trade.pnl, 
                        trade.pnl_pct, 
                        trade.sl_price, 
                        trade.t1_hit, 
                        trade.highest_pnl_pct
                    )
                    trade.last_db_update = datetime.now()
            else:
                trade.last_db_update = datetime.now()

            # --- GUARDRAIL: Prevent historical ticks from affecting live trades ---
            t_candle = candle.time
            t_entry = trade.entry_time
            if t_candle.tzinfo is not None and t_entry.tzinfo is None:
                t_candle = t_candle.replace(tzinfo=None)
            elif t_candle.tzinfo is None and t_entry.tzinfo is not None:
                t_entry = t_entry.replace(tzinfo=None)

            if t_candle < t_entry:
                continue

            # --- PER-TRADE EXIT LOGIC ---
            
            # 2. Expiry Day Hard Exit (Thursday 2:00 PM IST — NIFTY/BankNifty weekly expiry)
            if candle.time.weekday() == 3 and candle.time.hour >= 14:
                await self._close(trade, current_price, candle.time, "EXPIRY_HARD_EXIT")
                continue

            # 4. Theta-Based Mitigation (45 mins rule)
            duration_mins = (t_candle - t_entry).total_seconds() / 60
            if duration_mins > 45 and not trade.t1_hit:
                await self._close(trade, current_price, candle.time, "THETA_MITIGATION_45M")
                continue

            # 5. Dynamic Trailing Step Logic
            trade.highest_pnl_pct = max(trade.highest_pnl_pct, trade.pnl_pct)
            if trade.highest_pnl_pct >= 20.0:
                steps = int((trade.highest_pnl_pct - 20) / 10)
                trail_pnl_pct = steps * 10
                # Direction aware trailing
                mult = 1 if trade.direction == Direction.BUY else -1
                new_sl = round(trade.entry_price * (1 + (trail_pnl_pct / 100) * mult), 2)
                
                if (trade.direction == Direction.BUY and new_sl > trade.sl_price) or \
                   (trade.direction == Direction.SELL and new_sl < trade.sl_price):
                    print(f"📈 Step Trailing SL: {trade.sl_price} -> {new_sl} (Highest PnL: {trade.highest_pnl_pct}%)")
                    trade.sl_price = new_sl

            # 3. SL / T2 / Supertrend / T1 Logic
            if trade.direction == Direction.BUY:
                if current_price <= trade.sl_price:
                    await self._close(trade, trade.sl_price, candle.time, "SL")
                    continue
                elif current_price >= trade.t2_price:
                    await self._close(trade, trade.t2_price, candle.time, "T2")
                    continue
                
                # Supertrend Trailing (Only if symbols match exactly - NO OPTIONS)
                is_option = "-CE" in trade.instrument or "-PE" in trade.instrument or "_CE" in trade.instrument or "_PE" in trade.instrument
                if snapshot and "supertrend" in snapshot.indicators and not is_option:
                    st_val = snapshot.indicators["supertrend"].value
                    if isinstance(st_val, (int, float)) and st_val > trade.sl_price:
                        trade.sl_price = round(st_val, 2)
                        print(f"📈 Supertrend Trailing SL: {trade.sl_price} for {trade.instrument}")
                
                # T1 Breakeven Trailing
                if current_price >= trade.t1_price and not trade.t1_hit:
                    trade.t1_hit = True
                    if trade.sl_price < trade.entry_price:
                        trade.sl_price = trade.entry_price

            elif trade.direction == Direction.SELL:
                if current_price >= trade.sl_price:
                    await self._close(trade, trade.sl_price, candle.time, "SL")
                    continue
                elif current_price <= trade.t2_price:
                    await self._close(trade, trade.t2_price, candle.time, "T2")
                    continue
                
                # Supertrend Trailing (Only if symbol matches or not an option)
                is_option = "-CE" in trade.instrument or "-PE" in trade.instrument or "_CE" in trade.instrument or "_PE" in trade.instrument
                if snapshot and "supertrend" in snapshot.indicators and not is_option:
                    st_val = snapshot.indicators["supertrend"].value
                    if isinstance(st_val, (int, float)) and st_val < trade.sl_price:
                        trade.sl_price = round(st_val, 2)
                        print(f"📉 Supertrend Trailing SL: {trade.sl_price}")

                # T1 Breakeven Trailing
                if current_price <= trade.t1_price and not trade.t1_hit:
                    trade.t1_hit = True
                    if trade.sl_price > trade.entry_price:
                        trade.sl_price = trade.entry_price

        # 1. Daily Circuit Breaker Check (Global Drawdown > 5% of Total Capital)
        m = self.metrics()
        # Drawdown is (Realized + Open PnL) / Total Capital
        total_capital = self.risk_config.get("total_capital", 100000.0)
        current_drawdown_pct = round(((m["realized_pnl"] + m["open_pnl"]) / total_capital) * 100, 2)
        
        if current_drawdown_pct <= -5.0:
            print(f"🛑 CIRCUIT BREAKER: Daily drawdown reached {current_drawdown_pct}% (Limit: -5.0%)")
            # Close all with last known prices
            prices = {symbol: t.exit_price if t.status == PositionStatus.CLOSED else t.entry_price for symbol, t in self.open_positions.items()} # fallback
            await self.enable_kill_switch(prices, candle.time)
            return


    async def force_close_all(self, price_by_instrument: dict[str, float], now: datetime, reason: str) -> None:
        for trade in list(self.open_positions.values()):
            price = price_by_instrument.get(trade.instrument)
            
            # BACKTEST ENHANCEMENT: If live option price is missing, estimate based on underlying move
            if price is None and trade.underlying_entry_price:
                # Check BANKNIFTY first — "NIFTY" substring matches both
                underlying = "BANKNIFTY" if "BANKNIFTY" in trade.instrument else "NIFTY"
                underlying_price = price_by_instrument.get(underlying)
                if underlying_price:
                    move = underlying_price - trade.underlying_entry_price
                    delta = 0.5
                    direction_mult = 1 if "_CE" in trade.instrument else -1
                    price = round(trade.entry_price + (move * delta * direction_mult), 2)
                    price = max(0.01, price)

            if price is None:
                # Fallback for Options: Use entry price - slippage if no live price
                price = round(trade.entry_price * 0.9995, 2)
            await self._close(trade, price, now, reason)

    async def enable_kill_switch(self, price_by_instrument: dict[str, float], now: datetime) -> None:
        self.kill_switch_enabled = True
        await self.force_close_all(price_by_instrument, now, "KILL_SWITCH")

    def reset(self) -> None:
        self.open_positions.clear()
        self.closed_trades.clear()
        self.queued_signals.clear()
        self.kill_switch_enabled = False

    async def _close(self, trade: PaperTrade, price: float, at: datetime, reason: str) -> None:
        # Apply 0.05% slippage on exit
        slippage_factor = 0.9995 if trade.direction == Direction.BUY else 1.0005
        actual_price = round(price * slippage_factor, 2)

        # Consume rate limit token before "closing" order
        await order_rate_limiter.consume()

        trade.exit_price = actual_price
        trade.exit_time = at
        trade.exit_reason = reason
        trade.pnl = self._pnl(trade, actual_price)
        trade.pnl_pct = round((trade.pnl / (trade.entry_price * trade.quantity)) * 100, 2) if trade.quantity > 0 else 0.0
        trade.status = PositionStatus.CLOSED
        self.closed_trades.append(trade)
        self.open_positions.pop(trade.instrument, None)
        print(f"🏁 CLOSED: {trade.instrument} at {actual_price} Reason: {reason} ({at})")
        if not self.is_backtesting:
            persist_trade_exit(trade)
        training_logger.log_exit(trade)

    def _pnl(self, trade: PaperTrade, price: float) -> float:
        multiplier = 1 if trade.direction == Direction.BUY else -1
        return round((price - trade.entry_price) * trade.quantity * multiplier, 2)

    def metrics(self) -> dict[str, Any]:
        closed = self.closed_trades
        wins = [trade for trade in closed if trade.pnl > 0]
        losses = [abs(trade.pnl) for trade in closed if trade.pnl < 0]
        gross_profit = sum(trade.pnl for trade in wins)
        gross_loss = sum(losses)

        # Money In / Money Out
        # Total Invested is the sum of notional for all trades (entry_price * quantity)
        total_invested = sum(trade.entry_price * trade.quantity for trade in closed)
        total_invested += sum(trade.entry_price * trade.quantity for trade in self.open_positions.values())
        
        # Realized PnL is the net of (exit_price - entry_price) * quantity for closed trades
        realized_pnl = sum(trade.pnl for trade in closed)
        open_pnl = sum(trade.pnl for trade in self.open_positions.values())

        # Total Recovered is invested + pnl for closed trades
        total_recovered = total_invested - sum(trade.entry_price * trade.quantity for trade in self.open_positions.values()) + realized_pnl

        # Aggregate Percentages
        realized_pnl_pct = round((realized_pnl / total_invested) * 100, 2) if total_invested > 0 else 0.0
        open_pnl_pct = round((open_pnl / total_invested) * 100, 2) if total_invested > 0 else 0.0

        # Per Instrument Breakdown
        instruments = {}
        for symbol in set(t.instrument for t in (closed + list(self.open_positions.values()))):
            inst_closed = [t for t in closed if t.instrument == symbol]
            inst_wins = [t for t in inst_closed if t.pnl > 0]
            instruments[symbol] = {
                "trades": len(inst_closed),
                "win_rate": round((len(inst_wins) / len(inst_closed)) * 100, 2) if inst_closed else 0.0,
                "pnl": round(sum(t.pnl for t in inst_closed), 2),
                "invested": round(sum(t.entry_price * t.quantity for t in inst_closed), 2),
                "open_pnl": round(sum(t.pnl for t in self.open_positions.values() if t.instrument == symbol), 2)
            }

        return {
            "total_trades": float(len(closed)),
            "win_rate": round((len(wins) / len(closed)) * 100, 2) if closed else 0.0,
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else gross_profit,
            "realized_pnl": round(realized_pnl, 2),
            "realized_pnl_pct": realized_pnl_pct,
            "open_pnl": round(open_pnl, 2),
            "open_pnl_pct": open_pnl_pct,
            "total_invested": round(total_invested, 2),
            "total_recovered": round(total_recovered, 2),
            "instruments": instruments
        }

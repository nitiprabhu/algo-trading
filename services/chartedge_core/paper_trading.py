from __future__ import annotations
from uuid import UUID
from datetime import datetime, time
from typing import Any, Optional

from services.chartedge_core.option_data import bs_price, iv_from_vix
from datetime import timedelta
from services.chartedge_core.costs import option_entry_cost, option_exit_cost
from services.chartedge_core.database import (
    persist_trade_entry, persist_trade_exit, update_trade_mtm,
    get_open_trades, get_recent_closed_trades
)
from services.chartedge_core.training_logger import training_logger
from services.chartedge_core.models import Candle, Direction, IndicatorSnapshot, PaperTrade, PositionStatus, Signal
from services.chartedge_core.utils import order_rate_limiter


class PaperTradingEngine:
    def __init__(
        self,
        risk_config: dict,
        skip_db_load: bool = False,
        is_backtesting: bool = False,
        expiry_map: dict | None = None,
        costs_config: dict | None = None,
    ) -> None:
        self.risk_config = risk_config
        self.expiry_map = expiry_map or {}
        self.costs_config = costs_config or {}
        self.costs_enabled = self.costs_config.get("enabled", True)
        self.open_positions: dict[str, PaperTrade] = {}
        self.closed_trades: list[PaperTrade] = []
        self.queued_signals: list[Signal] = []
        self.kill_switch_enabled = False
        self.is_backtesting = is_backtesting
        self.consecutive_losses: int = 0
        self.cooldown_until: datetime | None = None
        # (underlying, option_type) pairs that hit a hard max-loss today — block same-side re-entry
        self.blocked_directions: set[tuple[str, str]] = set()
        if not skip_db_load and not is_backtesting:
            self.load_active_trades()


    def _dte_to_expiry(self, symbol: str, now: datetime) -> float:
        underlying = ("BANKNIFTY" if "BANKNIFTY" in symbol.upper() else "NIFTY" if "NIFTY" in symbol.upper() else symbol.upper())
        cfg = self.expiry_map.get(underlying, self.expiry_map.get("DEFAULT", {"weekly_weekday": 1, "monthly_weekday": 1}))
        d = now.date()
        weekly_wd = cfg.get("weekly_weekday")
        if weekly_wd is not None:
            expiry = d + timedelta(days=(weekly_wd - d.weekday()) % 7)
        else:
            monthly_wd = cfg.get("monthly_weekday", 1)
            import calendar as _cal
            last = _cal.monthrange(d.year, d.month)[1]
            expiry = d
            for day in range(last, last - 7, -1):
                if datetime(d.year, d.month, day).weekday() == monthly_wd:
                    expiry = datetime(d.year, d.month, day).date()
                    break
            if expiry < d:
                ny, nm = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
                last = _cal.monthrange(ny, nm)[1]
                expiry = d
                for day in range(last, last - 7, -1):
                    if datetime(ny, nm, day).weekday() == monthly_wd:
                        expiry = datetime(ny, nm, day).date()
                        break
        # Calculate strict DTE including time fraction
        expiry_dt = datetime.combine(expiry, time(15, 30))
        if hasattr(now, "tzinfo") and now.tzinfo:
            expiry_dt = expiry_dt.replace(tzinfo=now.tzinfo)
        diff = (expiry_dt - now).total_seconds() / 86400.0
        return max(0.001, diff)

    def load_active_trades(self) -> None:
        """Load open trades from the database to resume tracking after a restart."""
        records = get_open_trades()
        for r in records:
            if "_FUT" in r.symbol or r.symbol.endswith("_FUT"):
                continue
            from zoneinfo import ZoneInfo
            from datetime import timezone
            IST = ZoneInfo("Asia/Kolkata")
            entry_time = r.entry_time
            if entry_time is not None:
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=timezone.utc).astimezone(IST).replace(tzinfo=None)
                else:
                    entry_time = entry_time.astimezone(IST).replace(tzinfo=None)
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
        # Time Filter
        t = next_candle.time.time()
        open_cutoff = time(9, 45) if self.risk_config.get("avoid_first_30_mins", False) else time(9, 15)
        if t < open_cutoff or t >= time(15, 0):
            return None

        is_strategy_signal = getattr(signal, "strategy_name", "CONFLUENCE") not in ("CONFLUENCE", "")

        # Options-specific time gates
        is_option_entry = any(x in signal.instrument for x in ("-CE", "-PE", "_CE", "_PE"))
        if is_option_entry:
            # Hard rule: no options entries before 10:15 regardless of regime.
            # Opening 60 mins have violent price discovery — options get whipsawed.
            OPTIONS_OPEN_CUTOFF = time(10, 15)
            if not is_strategy_signal and t < OPTIONS_OPEN_CUTOFF:
                return None

            # Late-entry gate: need at least theta_timeout_mins before EOD
            theta = self.risk_config.get("theta_timeout_mins", 45)
            from datetime import datetime as _dt
            eod = _dt.combine(next_candle.time.date(), time(15, 0), tzinfo=next_candle.time.tzinfo)
            mins_to_eod = (eod - next_candle.time).total_seconds() / 60
            if mins_to_eod < theta:
                return None
            
        if self.kill_switch_enabled or signal.signal == Direction.HOLD:
            return None

        # Cooldown: block entries after 3 consecutive losses
        if self.cooldown_until is not None and next_candle.time < self.cooldown_until:
            return None

        if signal.confidence < self.risk_config["confidence_floor"]:
            return None

        # Options require extra conviction — marginal entries bleed theta + bid-ask without edge
        is_option_signal = any(x in signal.instrument for x in ("-CE", "-PE", "_CE", "_PE"))
        options_buffer = self.risk_config.get("options_confidence_buffer", 5)
        if is_option_signal and signal.confidence < self.risk_config["confidence_floor"] + options_buffer:
            print(f"⛔ [Options Buffer] {signal.instrument}: confidence {signal.confidence} below options floor {self.risk_config['confidence_floor'] + options_buffer}")
            return None

        # Options bias gate: regime agent may set a directional bias for the day.
        # Strategy signals (T315, 5EMA) bypass this — they are reactive intraday breakouts
        # that don't depend on prior-day direction. Bias only filters confluence-based signals.
        is_strategy_signal = bool(getattr(signal, "strategy_name", None))

        # Trend-gate: option buying only pays on directional days. Chop / mean-reverting
        # regimes bleed theta + spread. Skip them entirely unless explicitly disabled.
        # Applies to strategy signals (5EMA/T315) too — backtests show scalps bleed on
        # mean-reverting/chop days, so prior-day regime IS predictive of failure here.
        if is_option_signal and self.risk_config.get("trade_only_trending", True) and not self.is_backtesting:
            underlying_key = "BANKNIFTY" if "BANKNIFTY" in signal.instrument.upper() else "NIFTY"
            regime = self.risk_config.get(f"market_regime_{underlying_key}", "UNKNOWN")
            if regime in ("RANGE_BOUND_CHOP", "MEAN_REVERTING"):
                print(f"⛔ [Trend Gate] {signal.instrument}: regime {regime} — option buying skipped (chop bleed)")
                return None

        if is_option_signal and not is_strategy_signal:
            underlying_key = "BANKNIFTY" if "BANKNIFTY" in signal.instrument.upper() else "NIFTY"
            bias = self.risk_config.get(f"options_bias_{underlying_key}", "NEUTRAL")
            if bias != "NEUTRAL":
                opt_type = "CE" if ("-CE" in signal.instrument or "_CE" in signal.instrument) else "PE"
                if opt_type != bias:
                    print(f"⛔ [Bias Gate] {signal.instrument}: {opt_type} blocked — regime bias is {bias}")
                    return None

        # Trend-strength + directional filter. MFE data showed April's option buys fire
        # in near-FLAT structure (median ema50/200 sep ~0.07%) — weak/no trend, where
        # option buying gets whipsawed (aligned at entry, reverses after). So:
        #  (a) directional veto — never buy CE when ema50<<ema200 or PE when ema50>>ema200
        #      (harmless safety; confluence already aligns these, but guards edge cases);
        #  (b) strength gate — require the structure to actually be trending: either
        #      |ema50-ema200| sep >= options_min_trend_sep_pct OR ADX >= adx_min_trend.
        #      Below both = flat chop → skip the trade (this is the April fix).
        if is_option_signal and self.risk_config.get("options_directional_filter", True):
            snap = getattr(signal, "indicator_snapshot", None)
            inds = snap.indicators if snap is not None else {}
            ribbon = inds.get("ema_ribbon")
            ema_val = ribbon.value if (ribbon is not None and isinstance(ribbon.value, dict)) else {}
            ema50 = ema_val.get("ema50")
            ema200 = ema_val.get("ema200")
            adx_iv = inds.get("adx")
            adx_val = adx_iv.value if (adx_iv is not None and isinstance(adx_iv.value, (int, float))) else 0.0
            adx_min = self.risk_config.get("adx_min_trend", 20.0)
            if ema50 and ema200 and ema200 > 0:
                sep_pct = (ema50 - ema200) / ema200 * 100.0
                opt_type = "CE" if ("-CE" in signal.instrument or "_CE" in signal.instrument) else "PE"
                # (a) directional veto
                dir_sep = self.risk_config.get("dir_filter_min_sep_pct", 0.10)
                if opt_type == "CE" and sep_pct <= -dir_sep:
                    print(f"⛔ [Dir Filter] {signal.instrument}: CE blocked — down structure (sep {sep_pct:+.2f}%)")
                    return None
                if opt_type == "PE" and sep_pct >= dir_sep:
                    print(f"⛔ [Dir Filter] {signal.instrument}: PE blocked — up structure (sep {sep_pct:+.2f}%)")
                    return None
                # (b) trend-strength gate — skip flat chop
                min_trend_sep = self.risk_config.get("options_min_trend_sep_pct", 0.10)
                if abs(sep_pct) < min_trend_sep and adx_val < adx_min:
                    print(f"⛔ [Trend Strength] {signal.instrument}: flat structure (sep {sep_pct:+.2f}%, ADX {adx_val:.1f}) — option buy skipped")
                    return None

        # Directional re-entry block: if this underlying+side hit a hard max-loss today, skip
        u, opt = self._underlying_side(signal.instrument)
        if opt and (u, opt) in self.blocked_directions:
            print(f"🚫 [Dir Block] {signal.instrument}: {opt} re-entry blocked — hit MAX_LOSS earlier today")
            return None

        # Cross-instrument directional block: no correlated double-exposure.
        # NIFTY CE + BANKNIFTY CE open simultaneously = 2× loss on same market move.
        if is_option_signal:
            new_opt_type = "CE" if ("-CE" in signal.instrument or "_CE" in signal.instrument) else "PE"
            for open_trade in self.open_positions.values():
                open_ce = "-CE" in open_trade.instrument or "_CE" in open_trade.instrument
                open_pe = "-PE" in open_trade.instrument or "_PE" in open_trade.instrument
                if (new_opt_type == "CE" and open_ce) or (new_opt_type == "PE" and open_pe):
                    print(f"⛔ [Cross-Idx] {signal.instrument}: {new_opt_type} blocked — already holding {new_opt_type} on correlated index")
                    return None

        if signal.instrument in self.open_positions:
            self.queued_signals.append(signal)
            return None
        if len(self.open_positions) >= self.risk_config["max_open_positions"]:
            self.queued_signals.append(signal)
            return None

        # Apply 0.05% slippage on entry
        slippage_factor = 1.0005 if signal.signal == Direction.BUY else 0.9995
        
        is_multi_leg = bool(getattr(signal, "legs", []))
        is_option = is_multi_leg or any(x in signal.instrument for x in ("-CE", "-PE", "_CE", "_PE"))
        
        if is_multi_leg:
            # Net premium is already pre-calculated in signal.entry_price or computed here
            # But wait, signal doesn't have entry_price! We must compute it from legs.
            net_premium = 0.0
            for leg in signal.legs:
                multiplier = 1 if leg["action"] == "BUY" else -1
                net_premium += (leg.get("entry_price", leg.get("ltp", 0)) * leg.get("ratio", 1) * multiplier)
            entry_price = round(net_premium * slippage_factor, 2)
            # If it's a credit spread, entry price could be negative. For now, abs() or handle margins?
            # We'll use abs() to size risk correctly.
            entry_price = abs(entry_price)
        else:
            entry_price = round(next_candle.open * slippage_factor, 2)
        
        if is_option and not is_multi_leg:
            min_premium = self.risk_config.get("options_min_premium", 0.0)
            if entry_price < min_premium:
                print(f"⛔ [Premium Gate] {signal.instrument}: premium {entry_price} < {min_premium} — blocking cheap option")
                return None
        
        # PRD: Capital Allocation (2% Risk Limit of Total Equity)
        total_equity = self.risk_config.get("total_capital", 100000.0) 
        risk_per_trade = total_equity * 0.02 # 2% risk (2000 INR for 1L)
        
        # Risk per share calculation: Floor at 0.5% of entry to prevent infinite quantity on tight SL
        risk_per_share = max(abs(entry_price - signal.stop_loss), entry_price * 0.005)
        
        # Determine lot size and per-instrument lot cap
        inst_upper = signal.instrument.upper()
        if "BANKNIFTY" in inst_upper:
            lot_size = 15
            max_lots_cap = 4   # max 60 qty — premium ~₹18K at 300/lot; preserves capital
        elif "NIFTY" in inst_upper:
            lot_size = 75
            max_lots_cap = 1   # max 75 qty — premium ~₹12K at 160/lot (preserves 10% outlay cap)
        else:
            lot_size = 1
            max_lots_cap = 100

        # For Options (Symbols containing _CE or _PE), we also limit based on premium outlay
        # Senior Trader Rule: Max 10% of capital in a single options premium outlay (reduced from 15%)
        max_outlay = total_equity * 0.10

        raw_quantity = risk_per_trade / risk_per_share
        lots_by_risk = int(raw_quantity / lot_size)
        lots_by_outlay = int(max_outlay / (entry_price * lot_size))
        
        # Take the most conservative: risk, outlay, and hard lot cap
        lots = max(1, min(lots_by_risk, lots_by_outlay, max_lots_cap))

        # Conviction sizing: scale by signal confidence. High conviction = full size, weak = half.
        # confidence is 0-100. >=75 → full, 60-75 → 0.75x, <60 → 0.5x
        conf = signal.confidence
        conviction_mult = 1.0 if conf >= 75 else (0.75 if conf >= 60 else 0.5)
        if conviction_mult < 1.0:
            lots = max(1, int(lots * conviction_mult))
        print(f"DEBUG: Trade Calculation for {signal.instrument}: risk_per_share={risk_per_share:.2f}, lots_by_risk={lots_by_risk}, lots_by_outlay={lots_by_outlay}, conviction={conviction_mult}(conf={conf}), final_lots={lots}, lot_size={lot_size}")
        
        quantity = lots * lot_size
        invested_amount = round(entry_price * quantity, 2)
        
        # 1. Mutual Exclusion / Focus Rule Check
        has_active_futures = False
        used_futures_margin = 0.0
        if hasattr(self, "simulator") and self.simulator:
            active_fut = self.simulator.futures_trader.open_positions
            if len(active_fut) > 0:
                has_active_futures = True
            for fut_trade in active_fut.values():
                margin_pct = 0.12 if "BANKNIFTY" in fut_trade.instrument.upper() else 0.11
                used_futures_margin += fut_trade.entry_price * fut_trade.quantity * margin_pct
                
        if has_active_futures:
            print(f"⛔ [Margin Gate] {signal.instrument}: Blocked options entry because there is an active Futures position (Mutual Exclusion)")
            return None

        # 2. Free Margin Check
        used_options_outlay = sum(p.invested_amount for p in self.open_positions.values())
        free_margin = total_equity - used_options_outlay - used_futures_margin
        
        if invested_amount > free_margin:
            print(f"⛔ [Margin Gate] {signal.instrument}: Required premium outlay {invested_amount} exceeds free margin {free_margin:.2f} (Total Cap: {total_equity})")
            return None
            
        # Final safety check against hard capital limit
        if invested_amount > total_equity * 0.3: # Max 30% capital in one trade (even if risk is low)
            print(f"⚠️ Trade rejected: Invested amount ({invested_amount}) exceeds 30% capital buffer")
            return None
        # Options SL/T1/T2 must be in option-premium domain, not underlying domain.
        # AI/rule-based often returns levels in underlying index terms (e.g., SL=23900 for NIFTY).
        # Detect by: SL is wildly out of range vs entry_price (option premium).
        # NAKED_BUY:NIFTY / NAKED_BUY:BANKNIFTY trades are also premium-priced (net option premium),
        # so they need the same domain fix.
        is_multi_leg = ":" in signal.instrument
        is_option = is_multi_leg or any(x in signal.instrument for x in ("-CE", "-PE", "_CE", "_PE"))
        sl_price = signal.stop_loss
        t1_price = signal.target_1
        t2_price = signal.target_2
        if is_multi_leg:
            strategy_name = getattr(signal, "strategy_name", "")
            if strategy_name in ("IRON_CONDOR", "CREDIT_SPREAD"):
                # Credit strategies (SELL)
                sl_price = round(entry_price * 1.80, 2)
                t1_price = round(entry_price * 0.50, 2)
                t2_price = round(entry_price * 0.20, 2)
            elif strategy_name == "DEBIT_SPREAD":
                # Debit strategies (BUY)
                sl_price = round(entry_price * 0.50, 2)
                t1_price = round(entry_price * 1.50, 2)
                t2_price = round(entry_price * 2.00, 2)
            else:
                # Fallback for other structures
                sl_price = round(entry_price * 0.50, 2)
                t1_price = round(entry_price * 1.50, 2)
                t2_price = round(entry_price * 2.00, 2)
            print(f"⚠️ Multi-leg strategy {strategy_name} entry: SL={sl_price} T1={t1_price} T2={t2_price} (entry={entry_price})")
        elif is_option and (sl_price > entry_price * 3 or sl_price < 0):
            # SL is in underlying domain — translate to premium space via delta.
            # entry_delta is stored as abs(delta) by simulation.py; fall back to 0.50 if absent.
            # Use `is not None` guard so a genuine 0.0 delta doesn't silently become 0.50.
            raw_delta = signal.entry_delta if signal.entry_delta is not None else None
            delta = abs(raw_delta) if raw_delta is not None else 0.50
            if delta == 0.0:
                delta = 0.50  # explicit zero delta (deep expiry OTM) → use ATM default
            is_pe = "-PE" in signal.instrument or "_PE" in signal.instrument
            direction_mult = -1 if is_pe else 1
            if underlying_entry_price and underlying_entry_price > 0:
                sl_move = (signal.stop_loss - underlying_entry_price) * direction_mult * delta
                t1_move = (signal.target_1 - underlying_entry_price) * direction_mult * delta
                t2_move = (signal.target_2 - underlying_entry_price) * direction_mult * delta
                sl_price = round(max(entry_price + sl_move, entry_price * 0.80), 2)
                t1_price = round(entry_price + t1_move, 2)
                t2_price = round(entry_price + t2_move, 2)
                # Ensure sensible ordering (SL < entry < T1 < T2 for BUY)
                if signal.signal == Direction.BUY:
                    sl_price = min(sl_price, entry_price - 1)
                    t1_price = max(t1_price, entry_price + 1)
                    t2_price = max(t2_price, t1_price + 1)
            else:
                # No underlying price available — use conservative premium-% fallback
                sl_price = round(entry_price * 0.85, 2)
                t1_price = round(entry_price * 1.15, 2)
                t2_price = round(entry_price * 1.30, 2)
            print(f"⚠️ Options SL domain fix applied for {signal.instrument}: "
                  f"delta={delta:.2f} SL={sl_price} T1={t1_price} T2={t2_price} (entry={entry_price})")

        # Compute and record entry-side transaction costs immediately so open_pnl is honest
        costs_paid = 0.0
        if self.costs_enabled and is_option:
            default_spread_cfg = self.costs_config.get("default_spread", {})
            underlying_key = "BANKNIFTY" if "BANKNIFTY" in signal.instrument.upper() else "NIFTY"
            spread = default_spread_cfg.get(underlying_key, default_spread_cfg.get("DEFAULT", 2.0)) if isinstance(default_spread_cfg, dict) else 2.0
            costs_paid = round(option_entry_cost(entry_price, quantity, spread).total, 2)

        from services.chartedge_core.models import LegExecution
        trade_legs = []
        for leg_data in getattr(signal, "legs", []):
            trade_legs.append(LegExecution(
                instrument=leg_data["symbol"],
                action=Direction.BUY if leg_data["action"] == "BUY" else Direction.SELL,
                ratio=leg_data.get("ratio", 1),
                entry_price=leg_data.get("entry_price", leg_data.get("ltp", 0.0)),
                strike=leg_data.get("strike", 0.0),
                option_type=leg_data.get("option_type", "CE")
            ))

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
            pnl=-costs_paid,
            costs_paid=costs_paid,
            legs=trade_legs
        )
        # Consume rate limit token before "placing" order
        await order_rate_limiter.consume()
        
        print(f"🚀 ENTERED: {signal.instrument} {signal.signal} at {entry_price} ({next_candle.time})")
        trade_instrument = signal.instrument
        self.open_positions[trade_instrument] = trade
        if not self.is_backtesting:
            persist_trade_entry(trade)
            # Send Telegram Alert asynchronously
            import asyncio
            from services.chartedge_core.telegram import notifier
            msg = (
                f"🚀 *TRADE ENTERED*\n\n"
                f"🌐 *Instrument:* `{trade.instrument}`\n"
                f"📈 *Direction:* `{trade.direction.value}`\n"
                f"💰 *Entry Price:* `₹{trade.entry_price:.2f}`\n"
                f"📦 *Quantity:* `{trade.quantity}`\n"
                f"🛡️ *Stop Loss:* `₹{trade.sl_price:.2f}`\n"
                f"🎯 *Target 1:* `₹{trade.t1_price:.2f}`\n"
                f"🎯 *Target 2:* `₹{trade.t2_price:.2f}`\n\n"
            )
            if trade.legs:
                msg += "*⛓️ Leg Details:*\n"
                for leg in trade.legs:
                    msg += f"• *{leg.action.value}* `{leg.instrument}` @ `₹{leg.entry_price:.2f}` (Strike: {leg.strike})\n"
                msg += "\n"
            msg += f"🧠 *Reason:* {signal.reasoning or 'No reason provided.'}"
            asyncio.create_task(notifier.send_message(msg))
        training_logger.log_entry(trade, signal)
        return trade

    async def mark_to_market(self, candle: Candle, snapshot: IndicatorSnapshot | None = None, ltp_map: dict[str, float] | None = None) -> None:
        """Update all open positions. If ltp_map is provided (from live ticks), it takes priority."""
        for symbol, trade in list(self.open_positions.items()):
            # --- GUARDRAIL: Prevent historical ticks from affecting live trades ---
            t_candle = candle.time
            t_entry = trade.entry_time
            if t_candle.tzinfo is not None and t_entry.tzinfo is None:
                t_candle = t_candle.replace(tzinfo=None)
            elif t_candle.tzinfo is None and t_entry.tzinfo is not None:
                t_entry = t_entry.replace(tzinfo=None)

            if t_candle < t_entry:
                trade.pnl = 0.0
                trade.pnl_pct = 0.0
                continue

            current_price = None
            
            # Multi-leg logic
            if getattr(trade, "legs", []):
                net_price = 0.0
                all_legs_priced = True
                for leg in trade.legs:
                    leg_price = ltp_map.get(leg.instrument) if ltp_map else None
                    if leg_price is None and ltp_map:
                        stripped_leg = leg.instrument.split(":", 1)[1] if ":" in leg.instrument else leg.instrument
                        leg_price = ltp_map.get(stripped_leg)
                    if leg_price is None and self.is_backtesting and trade.underlying_entry_price:
                        # BS estimate
                        underlying_sym = "BANKNIFTY" if "BANKNIFTY" in trade.instrument.upper() else "NIFTY"
                        if candle.instrument == underlying_sym:
                            vix = 14.0 # default fallback
                            if hasattr(self, "candles") and "INDIAVIX" in self.candles and self.candles["INDIAVIX"]:
                                vix = self.candles["INDIAVIX"][-1].close
                            dte = self._dte_to_expiry(underlying_sym, candle.time)
                            iv = iv_from_vix(vix, dte)
                            leg_price = bs_price(candle.close, leg.strike, dte, iv, leg.option_type)
                            leg_price = max(0.01, round(leg_price, 2))
                    if leg_price is None:
                        all_legs_priced = False
                        break
                    multiplier = 1 if leg.action == Direction.BUY else -1
                    net_price += (leg_price * leg.ratio * multiplier)
                
                if all_legs_priced:
                    current_price = abs(round(net_price, 2))
            else:
                # Priority 1: Direct LTP from map (works for Options)
                if ltp_map and symbol in ltp_map:
                    current_price = ltp_map[symbol]
                # Priority 2: Current candle if symbols match
                elif symbol == candle.instrument:
                    current_price = candle.close
                
                # Fallback: try ltp_map by full instrument name
                if current_price is None:
                    if ltp_map and trade.instrument in ltp_map:
                        current_price = ltp_map[trade.instrument]
                    elif candle.instrument == trade.instrument:
                        current_price = candle.close
    
                # Backtest-only: synthesize option price from underlying move via delta.
                if current_price is None and self.is_backtesting:
                    is_opt = any(x in trade.instrument for x in ("-CE", "-PE", "_CE", "_PE"))
                    is_multi_leg = ":" in trade.instrument
                    if (is_opt or is_multi_leg) and trade.underlying_entry_price and trade.underlying_entry_price > 0:
                        underlying_sym = "BANKNIFTY" if "BANKNIFTY" in trade.instrument.upper() else "NIFTY"
                        if candle.instrument == underlying_sym:
                            is_pe = "-PE" in trade.instrument or "_PE" in trade.instrument
                            delta = 0.5  # ATM approximation for NAKED_BUY and -CE/-PE
                            direction_mult = -1 if is_pe else 1
                            underlying_move = candle.close - trade.underlying_entry_price
                            vix = 14.0 # default fallback
                            if "INDIAVIX" in self.candles and self.candles["INDIAVIX"]:
                                vix = self.candles["INDIAVIX"][-1].close
                            dte = self._dte_to_expiry(underlying_sym, candle.time)
                            iv = iv_from_vix(vix, dte)
                            
                            if is_multi_leg:
                                print(f"DEBUG MTM: instrument={trade.instrument} is_multi_leg={is_multi_leg} legs={len(trade.legs) if hasattr(trade, 'legs') else 'NO_ATTR'}")
                            if is_multi_leg and trade.legs:
                                net_premium = 0.0
                                for leg in trade.legs:
                                    leg_price = bs_price(candle.close, leg.strike, dte, iv, leg.option_type)
                                    multiplier = 1 if leg.action == Direction.BUY else -1
                                    net_premium += (leg_price * leg.ratio * multiplier)
                                current_price = max(0.01, round(net_premium, 2))
                                print(f"🔄 [MTM] {trade.instrument}: BS computed premium={current_price:.2f} (entry={trade.entry_price:.2f})")
                            else:
                                current_price = max(0.01, round(trade.entry_price + delta * direction_mult * underlying_move, 2))

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



            # --- PER-TRADE EXIT LOGIC ---
            
            # 2. Expiry Day Hard Exit (configured per instrument; default: Tuesday for NIFTY, Thursday for rest)
            if self._is_expiry_day(trade.instrument, candle.time) and candle.time.hour >= 14:
                await self._close(trade, current_price, candle.time, "EXPIRY_HARD_EXIT")
                continue

            # 4a. Hard max-loss guard: exit if premium down >13% (catastrophe guard, not regular SL).
            # Synthetic delta pricing in backtest is noisier than real options (no bid-ask smoothing).
            # 13% gives room for a ~26pt NIFTY adverse move before stopping out.
            is_option_pos = ":" in trade.instrument or any(x in trade.instrument for x in ("-CE", "-PE", "_CE", "_PE"))
            max_loss_pct = self.risk_config.get("options_max_loss_pct", 10.0)
            # Once the trail has locked the stop at/above breakeven, the catastrophe guard
            # must NOT override it. A winner that peaked (e.g. +24%) then reversed in one
            # 15-min synthetic step would otherwise be booked at -max_loss instead of the
            # locked profit — a real resting stop at the locked level fills there as price
            # falls through it. Defer to the trailed SL (section 3) for profit-locked trades;
            # the guard only governs positions still underwater (SL below entry).
            profit_locked = (
                trade.sl_price >= trade.entry_price
                if trade.direction == Direction.BUY
                else trade.sl_price <= trade.entry_price
            )
            if is_option_pos and not profit_locked and trade.pnl_pct < -max_loss_pct:
                # Synthetic premium only updates on 15-min underlying close, so a gap can
                # show -22% in one step. A real resting stop at -max_loss_pct would have
                # filled near that level intra-candle. Cap the fill at the stop price
                # (BUY-only premium longs) so the guard actually bounds loss size.
                if trade.direction == Direction.BUY:
                    guard_fill = round(trade.entry_price * (1 - max_loss_pct / 100), 2)
                    fill_price = max(current_price, guard_fill)
                else:
                    guard_fill = round(trade.entry_price * (1 + max_loss_pct / 100), 2)
                    fill_price = min(current_price, guard_fill)
                await self._close(trade, fill_price, candle.time, "MAX_LOSS_GUARD")
                continue

            # 4. Theta-Based Mitigation (regime-set timeout; extended by 30m if Supertrend still aligned)
            theta_mins = self.risk_config.get("theta_timeout_mins", 45)
            duration_mins = (t_candle - t_entry).total_seconds() / 60
            if duration_mins > theta_mins and not trade.t1_hit:
                st_trend_reversed = True  # default: close unless Supertrend confirms trend alive
                if snapshot and "supertrend" in snapshot.indicators:
                    st_vote = snapshot.indicators["supertrend"].vote
                    if trade.direction == Direction.BUY and st_vote == 1:
                        st_trend_reversed = False
                    elif trade.direction == Direction.SELL and st_vote == -1:
                        st_trend_reversed = False
                if st_trend_reversed or duration_mins > theta_mins + 30:
                    await self._close(trade, current_price, candle.time, f"THETA_MITIGATION_{theta_mins}M")
                    continue

            # 5. Dynamic Trailing Step Logic
            trade.highest_pnl_pct = max(trade.highest_pnl_pct, trade.pnl_pct)
            is_option = ":" in trade.instrument or "-CE" in trade.instrument or "-PE" in trade.instrument or "_CE" in trade.instrument or "_PE" in trade.instrument
            if is_option:
                if trade.direction == Direction.BUY:
                    if trade.highest_pnl_pct >= 8.0 and not trade.t1_hit:
                        trade.t1_hit = True
                        if trade.sl_price < trade.entry_price:
                            trade.sl_price = trade.entry_price
                            print(f"🛡️ Cost lock trailing SL: {trade.sl_price} for {trade.instrument} (Highest PnL: {trade.highest_pnl_pct}%)")
                    
                    # Percentage-of-peak trail: once a run is established (>=12% MFE),
                    # lock in a configurable fraction of the highest unrealized gain.
                    # MFE data showed winners peaking at +20/40% then reversing in one
                    # synthetic 15-min step and giving back ~45% of peak under the old
                    # coarse ladder. Locking a fixed fraction keeps more of each runner
                    # while still leaving room above the lock for trend continuation.
                    trail_keep = self.risk_config.get("options_trail_keep_frac", 0.65)
                    trail_arm = self.risk_config.get("options_trail_arm_pct", 12.0)
                    if trade.highest_pnl_pct >= trail_arm:
                        locked_pct = trade.highest_pnl_pct * trail_keep
                        new_sl = round(trade.entry_price * (1 + locked_pct / 100), 2)
                        if new_sl > trade.sl_price:
                            trade.sl_price = new_sl
                            print(f"📈 Peak-trail SL (+{locked_pct:.1f}%, {trail_keep:.0%} of peak {trade.highest_pnl_pct:.1f}%): {trade.sl_price} for {trade.instrument}")
                elif trade.direction == Direction.SELL:
                    if trade.highest_pnl_pct >= 8.0 and not trade.t1_hit:
                        trade.t1_hit = True
                        if trade.sl_price > trade.entry_price:
                            trade.sl_price = trade.entry_price
                            print(f"🛡️ Cost lock trailing SL: {trade.sl_price} for {trade.instrument} (Highest PnL: {trade.highest_pnl_pct}%)")
                    
                    trail_keep = self.risk_config.get("options_trail_keep_frac", 0.65)
                    trail_arm = self.risk_config.get("options_trail_arm_pct", 12.0)
                    if trade.highest_pnl_pct >= trail_arm:
                        locked_pct = trade.highest_pnl_pct * trail_keep
                        new_sl = round(trade.entry_price * (1 - locked_pct / 100), 2)
                        if new_sl < trade.sl_price:
                            trade.sl_price = new_sl
                            print(f"📈 Peak-trail SL (-{locked_pct:.1f}%, {trail_keep:.0%} of peak {trade.highest_pnl_pct:.1f}%): {trade.sl_price} for {trade.instrument}")
            else:
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
                
                # Supertrend Trailing (Only if symbols match exactly - NO OPTIONS or NAKED_BUY).
                # NAKED_BUY:NIFTY/BANKNIFTY SL is in premium space; Supertrend value is in index space — never mix.
                is_option = ":" in trade.instrument or "-CE" in trade.instrument or "-PE" in trade.instrument or "_CE" in trade.instrument or "_PE" in trade.instrument
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
                
                # Supertrend Trailing (Only if symbol matches or not an option/NAKED_BUY).
                # NAKED_BUY:NIFTY/BANKNIFTY SL is in premium space; Supertrend value is in index space — never mix.
                is_option = ":" in trade.instrument or "-CE" in trade.instrument or "-PE" in trade.instrument or "_CE" in trade.instrument or "_PE" in trade.instrument
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

        # 1. Daily Circuit Breaker: stop trading at -2% drawdown (₹2,000 on 1L capital).
        # -5% was too loose — allowed ₹5,000+ single-day wipeouts before stopping.
        m = self.metrics()
        total_capital = self.risk_config.get("total_capital", 100000.0)
        current_drawdown_pct = round(((m["realized_pnl"] + m["open_pnl"]) / total_capital) * 100, 2)

        if current_drawdown_pct <= -2.0:
            print(f"🛑 CIRCUIT BREAKER: Daily drawdown reached {current_drawdown_pct}% (Limit: -2.0%)")
            # Use ltp_map for current prices; fall back to entry_price if real price unavailable
            prices = {}
            for sym, t in self.open_positions.items():
                if ltp_map and sym in ltp_map:
                    prices[sym] = ltp_map[sym]
                elif sym == candle.instrument:
                    prices[sym] = candle.close
                else:
                    prices[sym] = t.entry_price
            await self.enable_kill_switch(prices, candle.time)
            return


    async def force_close_all(self, price_by_instrument: dict[str, float], now: datetime, reason: str) -> None:
        for trade in list(self.open_positions.values()):
            price = price_by_instrument.get(trade.instrument)
            if price is None:
                # Use last known PnL price (entry) rather than fabricating a delta estimate.
                # Better to record "closed at entry" than invent a wrong price.
                price = trade.entry_price
            await self._close(trade, price, now, reason)

    async def enable_kill_switch(self, price_by_instrument: dict[str, float], now: datetime) -> None:
        self.kill_switch_enabled = True
        await self.force_close_all(price_by_instrument, now, "KILL_SWITCH")

    def reset(self) -> None:
        self.open_positions.clear()
        self.closed_trades.clear()
        self.queued_signals.clear()
        self.kill_switch_enabled = False
        self.consecutive_losses = 0
        self.cooldown_until = None
        self.blocked_directions.clear()

    def _is_expiry_day(self, instrument: str, dt: datetime) -> bool:
        """True if dt falls on the expiry day for this instrument."""
        import calendar as _cal
        underlying = "BANKNIFTY" if "BANKNIFTY" in instrument.upper() else "NIFTY"
        inst_cfg = self.expiry_map.get(underlying, self.expiry_map.get("DEFAULT", {}))
        weekly_wd = inst_cfg.get("weekly_weekday")
        if weekly_wd is None:
            # No weekly — monthly expiry only (last Thursday of month)
            monthly_wd = inst_cfg.get("monthly_weekday", 3)
            last_day = _cal.monthrange(dt.year, dt.month)[1]
            # Walk back from month-end to find the last occurrence of monthly_wd
            for d in range(last_day, last_day - 7, -1):
                if datetime(dt.year, dt.month, d).weekday() == monthly_wd:
                    return dt.day == d
            return False
        return dt.weekday() == weekly_wd

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

        # Stale queued signals become invalid after a position closes — price has moved.
        # Executing them at a different time/price creates spurious re-entries.
        self.queued_signals.clear()

        # Deduct exit-side transaction costs (entry cost already charged at open via costs_paid)
        if self.costs_enabled:
            is_option = any(x in trade.instrument for x in ("-CE", "-PE", "_CE", "_PE"))
            if is_option:
                default_spread_cfg = self.costs_config.get("default_spread", {})
                underlying = "BANKNIFTY" if "BANKNIFTY" in trade.instrument.upper() else "NIFTY"
                spread = default_spread_cfg.get(underlying, default_spread_cfg.get("DEFAULT", 2.0)) if isinstance(default_spread_cfg, dict) else 2.0
                exit_cost = option_exit_cost(actual_price, trade.quantity, spread)
                trade.pnl = round(trade.pnl - exit_cost.total, 2)
                print(f"💸 Exit costs: ₹{exit_cost.total:.2f} (entry already charged: ₹{trade.costs_paid:.2f})")

        trade.pnl_pct = round((trade.pnl / (trade.entry_price * trade.quantity)) * 100, 2) if trade.quantity > 0 else 0.0
        trade.status = PositionStatus.CLOSED
        self.closed_trades.append(trade)
        self.open_positions.pop(trade.instrument, None)
        print(f"🏁 CLOSED: {trade.instrument} at {actual_price} Reason: {reason} ({at})")

        # Directional re-entry block on hard max-loss exits
        if reason == "MAX_LOSS_GUARD":
            u, opt = self._underlying_side(trade.instrument)
            if opt:
                self.blocked_directions.add((u, opt))
                print(f"⛔ [Dir Block] {u} {opt} side locked for rest of day (MAX_LOSS_GUARD)")

        # Consecutive loss tracker
        if trade.pnl < 0:
            self.consecutive_losses += 1
            halt_after = self.risk_config.get("daily_halt_after_losses", 7)
            cooldown_after = self.risk_config.get("cooldown_after_losses", 3)
            if self.consecutive_losses >= halt_after:
                print(f"🛑 KILL SWITCH: {self.consecutive_losses} consecutive losses — stopping for day")
                self.kill_switch_enabled = True
            elif self.consecutive_losses >= cooldown_after:
                from datetime import timedelta
                self.cooldown_until = at + timedelta(minutes=45)
                print(f"⏸️  COOLDOWN: {self.consecutive_losses} consecutive losses — no new entries until {self.cooldown_until.strftime('%H:%M')}")
        else:
            if self.consecutive_losses > 0:
                print(f"✅ Consecutive loss streak reset (was {self.consecutive_losses})")
            self.consecutive_losses = 0
            self.cooldown_until = None
        if not self.is_backtesting:
            persist_trade_exit(trade)
            # Send Telegram Alert asynchronously
            import asyncio
            from services.chartedge_core.telegram import notifier
            emoji = "🟢" if trade.pnl >= 0 else "🔴"
            msg = (
                f"🏁 *TRADE CLOSED*\n\n"
                f"🌐 *Instrument:* `{trade.instrument}`\n"
                f"📉 *Exit Price:* `₹{trade.exit_price:.2f}`\n"
                f"🚪 *Exit Reason:* `{trade.exit_reason}`\n"
                f"{emoji} *PnL:* `₹{trade.pnl:.2f}` ({trade.pnl_pct:.2f}%)\n\n"
            )
            if trade.legs:
                msg += "*⛓️ Leg Details:*\n"
                for leg in trade.legs:
                    exit_price_str = f"₹{leg.exit_price:.2f}" if leg.exit_price is not None else "N/A"
                    msg += f"• *{leg.action.value}* `{leg.instrument}` (Entry: `₹{leg.entry_price:.2f}`, Exit: `{exit_price_str}`)\n"
                msg += "\n"
            msg += f"💵 *Invested Amount:* `₹{trade.invested_amount:.2f}`"
            asyncio.create_task(notifier.send_message(msg))
        training_logger.log_exit(trade)

    def _underlying_side(self, instrument: str) -> tuple[str, str | None]:
        """Extract (underlying, option_type) e.g. NIFTY-Jun2026-23800-PE → ('NIFTY', 'PE')."""
        opt = None
        if "-CE" in instrument or "_CE" in instrument:
            opt = "CE"
        elif "-PE" in instrument or "_PE" in instrument:
            opt = "PE"
        underlying = "BANKNIFTY" if "BANKNIFTY" in instrument.upper() else ("NIFTY" if "NIFTY" in instrument.upper() else instrument)
        return underlying, opt

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

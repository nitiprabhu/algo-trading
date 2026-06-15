from __future__ import annotations

import asyncio
import math
import random
from collections import defaultdict, deque
from datetime import datetime, timedelta, time, date
from zoneinfo import ZoneInfo

import uuid
from services.chartedge_core.ai_signal import SignalEngine
from services.chartedge_core.config import Config
from services.chartedge_core.confluence import score
from services.chartedge_core.indicators import compute_snapshot_indicators
from services.chartedge_core.models import Candle, DashboardSnapshot, Direction, IndicatorSnapshot, Signal
from services.chartedge_core.option_data import bs_price, bs_delta, iv_from_vix
from services.chartedge_core.paper_trading import PaperTradingEngine
from services.chartedge_core.futures_trader import FuturesTradingEngine
from services.chartedge_core.training_logger import training_logger, options_logger
from services.chartedge_core.regime_detector import RegimeDetector


IST = ZoneInfo("Asia/Kolkata")


class MarketSimulator:
    def __init__(self, config: Config, skip_db_load: bool = False) -> None:
        self.config = config
        self.candles: dict[str, deque[Candle]] = {
            symbol: deque(maxlen=260) for symbol in config.enabled_symbols
        }
        self.latest_indicators: dict[str, IndicatorSnapshot] = {}
        self._token_ltp: dict[str, float] = {} # Real-time LTP map for all subscribed tokens
        self.signals: list[Signal] = []
        self.equity_curve: list[dict[str, float | str]] = []
        self.market_data_history: dict[str, deque[dict[str, float | str]]] = {
            symbol: deque(maxlen=260) for symbol in config.enabled_symbols
        }
        self.feed_health = "WARMING"
        self.trader = PaperTradingEngine(
            config.risk,
            skip_db_load=skip_db_load,
            expiry_map=getattr(config, "expiry_map", {}),
            costs_config=getattr(config, "costs_config", {}),
        )
        self.signal_engine = SignalEngine(config.ai, config.confluence_thresholds)
        self.last_signal_times: dict[tuple[str, str], datetime] = {}
        self._running = False
        self.is_seeding = False
        self.is_backtesting = False
        self._last_trading_date: object = None
        self.regime_detectors: dict[str, RegimeDetector] = {
            symbol: RegimeDetector() for symbol in config.enabled_symbols
        }
        # Futures engine — separate from options PaperTradingEngine
        futures_risk_cfg = getattr(config, "futures_risk", {})
        self.futures_trader = FuturesTradingEngine(
            futures_risk_cfg=futures_risk_cfg,
            is_backtesting=skip_db_load,  # skip_db_load == True during backtests
            risk_config=config.risk,
        )
        self.trader.simulator = self
        self.futures_trader.simulator = self

    async def seed(self) -> None:
        now = datetime.now(IST).replace(second=0, microsecond=0) - timedelta(minutes=180)
        bases = {
            "NIFTY": 22850.0, 
            "BANKNIFTY": 48600.0,
            "RELIANCE": 2950.0,
            "HDFCBANK": 1530.0,
            "INDIAVIX": 12.5
        }
        for symbol in self.config.enabled_symbols:
            price = bases.get(symbol, 1000.0)
            for idx in range(180):
                # VIX has less volatility
                vol = 0.5 if symbol == "INDIAVIX" else 7
                price += math.sin(idx / 9) * vol + random.uniform(-vol*1.5, vol*1.5)
                price = max(0.1, price)
                candle = self._make_candle(symbol, now + timedelta(minutes=idx), price)
                self.candles[symbol].append(candle)
                self.market_data_history[symbol].append({"time": candle.time.isoformat(), "price": candle.close})
        self.feed_health = "OK"

    async def run(self) -> None:
        if not self.candles["NIFTY"]:
            await self.seed()
        self._running = True
        while self._running:
            await self.step()
            await asyncio.sleep(2)

    async def step(self) -> None:
        now = datetime.now(IST).replace(second=0, microsecond=0)
        
        # Guardrail: Check if market is open (9:15 - 15:30 IST, Weekdays, Non-Holidays)
        if not self._is_market_open(now):
            # Still update feed health but skip trade processing
            self.feed_health = "MARKET_CLOSED"
            return

        for symbol in self.config.enabled_symbols:
            previous = self.candles[symbol][-1].close
            drift = 9 * math.sin(len(self.candles[symbol]) / 11) + random.uniform(-18, 18)
            candle = self._make_candle(symbol, now, previous + drift)
            await self.process_closed_candle(candle)

        self._append_equity(now)
        self.feed_health = "OK"

    def _is_trading_day(self, dt: datetime) -> bool:
        """Check if today is a valid trading day (not weekend or public holiday)."""
        if dt.weekday() >= 5:
            return False
            
        holidays = [
            "2026-01-26", # Republic Day
            "2026-03-06", # Holi (Example)
            "2026-04-02", # Good Friday (Example)
            "2026-04-14", # Ambedkar Jayanti
            "2026-05-01", # Maharashtra Day / Labor Day
            "2026-08-15", # Independence Day
            "2026-10-02", # Gandhi Jayanti
            "2026-12-25", # Christmas
        ]
        if dt.strftime("%Y-%m-%d") in holidays:
            return False
            
        return True

    def _is_market_open(self, dt: datetime) -> bool:
        """Check if the market is open based on IST time, weekends, and holidays."""
        if not self._is_trading_day(dt):
            return False
            
        # 2. Market Hours (09:15 - 15:30 IST)
        t = dt.time()
        if t < time(9, 15) or t > time(15, 30):
            return False
            
        return True

    def get_option_contract(self, symbol: str, direction: str) -> Optional[dict]:
        """Stub for resolving options - overridden in IndstocksMarketRuntime."""
        return None

    def get_multi_leg_structure(self, symbol: str, direction: str, strategy_name: str | None = None) -> Optional[dict]:
        """Stub for resolving multi-leg options - overridden in IndstocksMarketRuntime."""
        return None

    def _structure_allows_entry(self, symbol: str, direction: str, strategy_name: str | None = None) -> bool:
        """Return False to block an option entry. Override in live runtime for regime gating."""
        return True

    def _get_structure_strike_offset(self, symbol: str) -> int:
        """Return regime-aware strike offset. Override in live runtime."""
        return int(self.config.risk.get("options_strike_offset", 0))

    @staticmethod
    def _last_weekday_of_month(year: int, month: int, weekday: int):
        """Date of the last <weekday> (0=Mon..6=Sun) in a given month."""
        import calendar as _cal
        last = _cal.monthrange(year, month)[1]
        for day in range(last, last - 7, -1):
            if datetime(year, month, day).weekday() == weekday:
                return datetime(year, month, day).date()
        return datetime(year, month, last).date()

    def _dte_to_expiry(self, symbol: str, now: datetime) -> float:
        """Real days-to-expiry for the nearest contract per NSE rules (config expiry_map).

        Replaces the old flat `dte = 7`. NIFTY = weekly Tuesday; BANKNIFTY = monthly
        (last Tuesday, no weekly). Includes the intraday fraction still to run so theta
        is right on/near expiry day. Falls back to 3 days if no map.
        """
        underlying = ("BANKNIFTY" if "BANKNIFTY" in symbol.upper()
                      else "NIFTY" if "NIFTY" in symbol.upper() else symbol.upper())
        emap = getattr(self.trader, "expiry_map", {}) or {}
        cfg = emap.get(underlying, emap.get("DEFAULT", {"weekly_weekday": 1, "monthly_weekday": 1}))
        d = now.date()
        weekly_wd = cfg.get("weekly_weekday")
        if weekly_wd is not None:
            expiry = d + timedelta(days=(weekly_wd - d.weekday()) % 7)
        else:
            monthly_wd = cfg.get("monthly_weekday", 1)
            expiry = self._last_weekday_of_month(d.year, d.month, monthly_wd)
            if expiry < d:
                ny, nm = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
                expiry = self._last_weekday_of_month(ny, nm, monthly_wd)
        # fraction of the current trading day (to ~15:30) still remaining
        frac = max(0.0, (15.5 - (now.hour + now.minute / 60.0))) / 24.0
        return max(0.2, (expiry - d).days + frac)

    async def process_closed_candle(self, candle: Candle) -> None:
        if candle.time.minute % 30 == 0:
            print(f"DEBUG: Processing candles at {candle.time}...")
        symbol = candle.instrument
        self.candles[symbol].append(candle)
        self.market_data_history[symbol].append({"time": candle.time.isoformat(), "price": candle.close})

        # Day-boundary reset: clear per-day state when date changes (multi-day backtest support)
        candle_date = candle.time.date()
        if self._last_trading_date != candle_date:
            if self._last_trading_date is not None:
                print(f"📅 New trading day {candle_date} — resetting daily state (kill_switch, T315 lock, loss streak)")
                self.trader.kill_switch_enabled = False
                self.trader.queued_signals.clear()
                self.trader.consecutive_losses = 0
                self.trader.cooldown_until = None
                self.trader.blocked_directions.clear()
                self.futures_trader.reset_daily_state()
                self.signal_engine.t315_direction_lock.clear()
            self._last_trading_date = candle_date

        # Only trading symbols can have positions and signals
        if symbol in self.config.trading_symbols:
            snapshot = self.latest_indicators.get(symbol)
            await self.trader.mark_to_market(candle, snapshot, ltp_map=self._token_ltp)

            # Futures MTM: update on every NIFTY candle
            if symbol == "NIFTY" and self.futures_trader.open_positions:
                st_ind = snapshot.indicators.get("supertrend") if snapshot else None
                st_val = st_ind.value if (st_ind and isinstance(st_ind.value, (int, float))) else None
                await self.futures_trader.mark_to_market(candle, supertrend_value=st_val)
            
            # 2. Strategy-specific exits and SL updates
            for trade in list(self.trader.open_positions.values()):
                # Only update SL if trade instrument relates to this candle instrument
                if not trade.instrument.startswith(symbol):
                    continue
                    
                # Trailing SL update if Supertrend moved
                st_ind = snapshot.indicators.get("supertrend") if snapshot else None
                if st_ind:
                    # FOR OPTIONS: Don't use underlying supertrend value as hard SL price
                    is_option = "-CE" in trade.instrument or "-PE" in trade.instrument or "_CE" in trade.instrument or "_PE" in trade.instrument
                    if is_option:
                        continue # Skip direct SL update for options (they use translated SL)
                    
                    new_sl = st_ind.value
                    if (trade.direction == Direction.BUY and new_sl > trade.sl_price) or \
                       (trade.direction == Direction.SELL and new_sl < trade.sl_price):
                        print(f"📈 Supertrend Trailing SL: {new_sl} for {trade.instrument}")
                        trade.sl_price = new_sl
            
            # F&O specific strategy check (1m resolution)
            vix_val = self.candles.get("INDIAVIX")[-1].close if self.candles.get("INDIAVIX") else 0.0
            # Push live VIX into risk_config so paper_trading VIX gate can read it
            self.trader.risk_config["current_vix"] = vix_val
            fo_signal = await self.signal_engine.get_fo_signal(candle, list(self.candles[symbol]), vix_val, snapshot)
            if fo_signal:
                # Intraday ADX trend gate for strategy scalps (5EMA/T315).
                # Prior-day regime mislabels intraday trend days as chop; use live ADX instead.
                # Option-buying scalps bleed on no-trend bars — require real intraday momentum.
                if self.config.risk.get("strategy_adx_gate", True):
                    adx_ind = snapshot.indicators.get("adx") if snapshot else None
                    adx_min = self.config.risk.get("adx_min_trend", 20.0)
                    if adx_ind is not None and 0 < adx_ind.value < adx_min:
                        print(f"🚫 [ADX Gate] {symbol}: {fo_signal.strategy_name} blocked — ADX {adx_ind.value:.1f} < {adx_min} (no intraday trend)")
                        return

                # Rate Limit Guard
                if self._is_rate_limited(symbol, fo_signal.strategy_name, candle.time):
                    return

                # Silence signals during seeding (initial startup) to avoid dashboard spam.
                # But allow them if we are explicitly in backtesting mode.
                if not self.is_backtesting and (datetime.now(IST) - candle.time).total_seconds() > 300:
                    # It's historical seeding, just insert into list but don't "trigger" entry
                    pass
                else:
                    self.last_signal_times[(symbol, fo_signal.strategy_name)] = candle.time
                    self.last_signal_times[(symbol, "GLOBAL")] = candle.time
                    # _process_and_enter now handles signal insertion to avoid duplicates
                    await self._process_and_enter(fo_signal, candle)

        # Mandatory EOD Square-off from config (targeting 15:00 IST)
        # Rule: Only trigger square-off if NOT seeding/backfilling and it's the current day
        sq_hour, sq_min = map(int, self.config.market_hours["square_off"].split(":"))
        if candle.time.hour > sq_hour or (candle.time.hour == sq_hour and candle.time.minute >= sq_min):
            if not self.is_seeding:
                eod_date = candle.time.date()
                if not hasattr(self, "_eod_squared_date") or self._eod_squared_date != eod_date:
                    self._eod_squared_date = eod_date
                    print(f"🕒 EOD Boundary Hit at {candle.time}")
                    if self.trader.open_positions:
                        prices = {s: self.candles[s][-1].close for s in self.candles if self.candles[s]}
                        await self.trader.force_close_all(prices, candle.time, "EOD_SQUAREOFF")
            return  # Stop processing further (no new entries)

        # Only process signals for trading symbols
        if symbol not in self.config.trading_symbols:
            return

        interval_mins = int(self.config.risk.get("timeframe_mins", 15))
        if not self._is_timeframe_boundary(candle.time, interval_mins):
            return

        if len(self.candles[symbol]) < (interval_mins * 2): # Ensure enough data
            return

        # Build the aggregated candle
        candles_agg = self._aggregate_candles(symbol, interval_mins)

        weights = self.config.indicator_weights.get(symbol, {})
        indicators = compute_snapshot_indicators(candles_agg, weights)

        # Update RegimeDetector with latest vol/trend metrics
        vix_val = self.candles.get("INDIAVIX")[-1].close if self.candles.get("INDIAVIX") else 16.0
        atr_val = indicators.get("atr").value if indicators.get("atr") else 0.0
        adx_val = indicators.get("adx").value if indicators.get("adx") else 0.0
        self.regime_detectors[symbol].update(candle.time, vix_val, atr_val, adx_val)
        regime_summary = self.regime_detectors[symbol].summary()

        snapshot = IndicatorSnapshot(
            instrument=symbol,
            timeframe=f"{interval_mins}m",
            candle_time=candle.time,
            price=candle.close,
            indicators=indicators,
            confluence_score=score(indicators),
            higher_timeframe=self._higher_timeframe(symbol),
            market_context=self._get_market_context(),
            options_data=self._get_options_data(symbol),
            regime_info=regime_summary,
        )
        self.latest_indicators[symbol] = snapshot
        training_logger.log_snapshot(snapshot)
        options_logger.log_options_state(snapshot)
        
        signal = await self.signal_engine.generate(snapshot, candles_agg)
        
        if signal.signal != Direction.HOLD:
             print(f"📡 SIGNAL ({symbol}): {signal.signal} Confidence: {signal.confidence}%")
        
        # ADX Trend Gate: skip confluence entries on choppy/no-trend bars (options bleed in chop)
        if signal.signal != Direction.HOLD:
            adx_ind = snapshot.indicators.get("adx") if snapshot else None
            adx_min = self.config.risk.get("adx_min_trend", 20.0)
            if adx_ind is not None and 0 < adx_ind.value < adx_min:
                print(f"🚫 [ADX Gate] {symbol}: {signal.signal.value} blocked — ADX {adx_ind.value} < {adx_min} (no trend)")
                return

        # T315 Direction Lock Guard: block confluence entries opposite to today's T315 breakout
        if signal.signal != Direction.HOLD:
            lock = self.signal_engine.t315_direction_lock.get(symbol)
            if lock:
                locked_opt, locked_date = lock
                if locked_date == candle_date:
                    # CE lock → only BUY signals allowed; PE lock → only SELL signals allowed
                    conflict = (locked_opt == "CE" and signal.signal == Direction.SELL) or \
                               (locked_opt == "PE" and signal.signal == Direction.BUY)
                    if conflict:
                        print(f"🚫 [T315 Lock] {symbol}: confluence {signal.signal.value} blocked — T315 {locked_opt} breakout active today")
                        return

        # Rate Limit Guard
        if signal.signal != Direction.HOLD:
            if self._is_rate_limited(symbol, "CONFLUENCE", candle.time):
                return
            
            # Silence historical signals during seeding (initial startup).
            # But allow them if we are explicitly in backtesting mode.
            if not self.is_backtesting and (datetime.now(IST) - candle.time).total_seconds() > 300:
                pass
            else:
                self.last_signal_times[(symbol, "CONFLUENCE")] = candle.time
                self.last_signal_times[(symbol, "GLOBAL")] = candle.time
                await self._process_and_enter(signal, candle)

    def _is_rate_limited(self, symbol: str, strategy: str, now: datetime) -> bool:
        """
        Enforces:
        1. Per-strategy limit (max_signals_per_instrument_per_hour).
        2. Global instrument-level cooldown (min 15 mins between ANY signal for the same instrument).
        """
        # Handle timezone awareness for comparisons
        def ensure_tz(dt, reference):
            if reference.tzinfo is not None and dt.tzinfo is None:
                return dt.replace(tzinfo=reference.tzinfo)
            if reference.tzinfo is None and dt.tzinfo is not None:
                return dt.replace(tzinfo=None)
            return dt

        # 1. Global Cooldown Check (15 mins)
        last_global = self.last_signal_times.get((symbol, "GLOBAL"))
        if last_global:
            lg = ensure_tz(last_global, now)
            if (now - lg).total_seconds() < 900:
                return True

        # 2. Per-Strategy Check
        last_time = self.last_signal_times.get((symbol, strategy))
        if not last_time:
            return False
            
        lt = ensure_tz(last_time, now)
        limit = self.config.risk.get("max_signals_per_instrument_per_hour", 3)
        # Average gap for 3/hour is 20 mins, let's enforce a 20 min gap for the same strategy
        if (now - lt).total_seconds() < 1200: 
            return True
        return False

    async def _process_and_enter(self, signal: Signal, candle: Candle) -> None:
        """Shared logic to handle option proxying and entry."""
        symbol = signal.instrument
        
        next_open = Candle(
            time=candle.time + timedelta(minutes=1),
            instrument=symbol,
            timeframe=candle.timeframe,
            open=candle.close,
            high=candle.close,
            low=candle.close,
            close=candle.close,
            volume=0,
        )

        # T315 regime gates (before option proxying):
        # 1. Block T315 on RANGE_BOUND_CHOP — ORB momentum fails on range-bound days.
        # 2. Block T315 when its direction contradicts the regime options_bias.
        strategy_name = getattr(signal, 'strategy_name', None)
        if strategy_name == 'T315' and symbol in ["NIFTY", "BANKNIFTY"]:
            regime = getattr(self, '_regime_by_symbol', {}).get(symbol, "")
            if regime == "RANGE_BOUND_CHOP":
                print(f"⛔ [T315 Regime] {symbol} T315 blocked — RANGE_BOUND_CHOP (ORB momentum unreliable)")
                return
            opt_type = getattr(signal, 'option_type', None)
            bias = self.trader.risk_config.get(f"options_bias_{symbol}", "NEUTRAL")
            if bias != "NEUTRAL" and opt_type and opt_type != bias:
                print(f"⛔ [T315 Bias] {symbol} T315 {opt_type} blocked — regime bias is {bias}")
                return

        # FUTURES ROUTING: NIFTY_FUT signals go directly to FuturesTradingEngine
        if signal.instrument == "NIFTY_FUT":
            strategy_name = getattr(signal, "strategy_name", None) or ""
            if strategy_name.startswith("FUT_") and self.config.risk.get("trade_only_trending", True) and not self.is_backtesting:
                regime = getattr(self, "_regime_by_symbol", {}).get("NIFTY", "")
                if regime in ("RANGE_BOUND_CHOP", "MEAN_REVERTING"):
                    print(
                        f"⛔ [FUT Regime] {strategy_name} blocked — {regime} "
                        "(ORB momentum unreliable on chop days)"
                    )
                    return
            print(f"🔀 [Router] {signal.instrument} futures signal → FuturesTradingEngine")
            self.signals.insert(0, signal)
            self.signals = self.signals[:80]
            # Use the NIFTY candle price (spot ≈ futures in simulation)
            fut_candle = next_open.model_copy()
            
            base_symbol = symbol.replace("_FUT", "")
            expiry_suffix = ""
            if getattr(self, "dm", None):
                expiry_suffix = self.dm.get_futures_expiry_suffix(base_symbol)
                
            fut_candle.instrument = f"{base_symbol}_FUT{expiry_suffix}"
            signal.instrument = f"{base_symbol}_FUT{expiry_suffix}"
            
            await self.futures_trader.maybe_enter(signal, fut_candle)
            return

        # OPTIONS PROXY LOGIC: Check for ATM option contract if it's a trading index
        if symbol in ["NIFTY", "BANKNIFTY"] and signal.signal != Direction.HOLD:
            preferred_side_check = signal.option_type if signal.option_type else signal.signal.value
            strategy_name = getattr(signal, "strategy_name", None)
            if not self._structure_allows_entry(symbol, preferred_side_check, strategy_name):
                print(f"🚫 [Structure Gate] {symbol} {preferred_side_check} blocked by regime/IV filter")
                return
            # If the signal has an explicit option_type (CE/PE), use it. Otherwise fallback to sentiment.
            preferred_side = signal.option_type if signal.option_type else signal.signal.value
            multi_leg = self.get_multi_leg_structure(symbol, preferred_side, strategy_name)
            
            if multi_leg and multi_leg.get("legs"):
                print(f"🎯 Creating Multi-Leg Trade for {symbol}: {multi_leg['strategy_name']}")
                opt_signal = signal.model_copy()
                opt_signal.id = uuid.uuid4()
                expiry_suffix = multi_leg.get("expiry_suffix", "")
                opt_signal.instrument = f"{multi_leg['strategy_name']}:{symbol}{expiry_suffix}"
                opt_signal.strategy_name = multi_leg["strategy_name"]
                
                resolved_legs = []
                net_premium = 0.0
                est_delta = 0.0
                
                for leg in multi_leg["legs"]:
                    premium = leg["ltp"]
                    delta = 0.5
                    if premium <= 0:
                        vix = self._token_ltp.get("40000107") or self._token_ltp.get("INDIAVIX") or 14.0
                        if self.candles.get("INDIAVIX"):
                            vix = self.candles["INDIAVIX"][-1].close
                        opt_type = leg["option_type"]
                        strike = leg.get("strike", candle.close)
                        dte = self._dte_to_expiry(symbol, candle.time)
                        iv = iv_from_vix(vix, dte)
                        premium = bs_price(candle.close, strike, dte, iv, opt_type)
                        delta = bs_delta(candle.close, strike, dte, iv, opt_type)
                        
                    leg["entry_price"] = premium
                    resolved_legs.append(leg)
                    multiplier = 1 if leg["action"] == "BUY" else -1
                    net_premium += (premium * leg.get("ratio", 1) * multiplier)
                    est_delta += (abs(delta) * leg.get("ratio", 1) * multiplier)
                    
                opt_signal.legs = resolved_legs
                opt_signal.entry_delta = abs(est_delta)
                opt_signal.signal = Direction.SELL if net_premium < 0 else Direction.BUY

                
                # Update SL and targets for the overall structure (optional mapping via delta proxy if needed)
                # For Multi-Leg, since maybe_enter applies direction and delta internally to compute exit targets,
                # we pass down the absolute net_premium as the baseline.
                # Actually, maybe_enter handles SL/T1/T2 conversion inside paper_trading.py using `entry_delta`.
                
                opt_open = next_open.model_copy()
                opt_open.instrument = opt_signal.instrument
                opt_open.open = net_premium
                
                # Add the option signal to the history list
                self.signals.insert(0, opt_signal)
                
                # Execute Option Trade
                await self.trader.maybe_enter(opt_signal, opt_open, underlying_entry_price=candle.close)
                
                # Exit early - we don't want to trade the index directly if we're trading options
                return

        # STOCK LOGIC: If we reach here, it's not a proxied index (like RELIANCE, HDFCBANK)
        self.signals.insert(0, signal)
        self.signals = self.signals[:80]
        await self.trader.maybe_enter(signal, next_open)

    def _is_timeframe_boundary(self, time: datetime, interval: int) -> bool:
        """Check if the candle time falls on a timeframe boundary."""
        interval = int(interval)
        return (time.minute + 1) % interval == 0

    def _aggregate_candles(self, symbol: str, interval: int) -> list[Candle]:
        """Aggregate the stored 1m candles into bars for indicator calculation."""
        interval = int(interval)
        raw = list(self.candles[symbol])
        if len(raw) < interval:
            return raw

        bars: list[Candle] = []
        i = 0
        # Align to boundaries
        while i < len(raw) and raw[i].time.minute % interval != 0:
            i += 1

        while i + (interval - 1) < len(raw):
            chunk = raw[i:i + interval]
            bar = Candle(
                time=chunk[0].time,
                instrument=symbol,
                timeframe=f"{interval}m",
                open=chunk[0].open,
                high=max(c.high for c in chunk),
                low=min(c.low for c in chunk),
                close=chunk[-1].close,
                volume=sum(c.volume for c in chunk),
            )
            bars.append(bar)
            i += interval

        if i < len(raw):
            chunk = raw[i:]
            bar = Candle(
                time=chunk[0].time,
                instrument=symbol,
                timeframe=f"{interval}m",
                open=chunk[0].open,
                high=max(c.high for c in chunk),
                low=min(c.low for c in chunk),
                close=chunk[-1].close,
                volume=sum(c.volume for c in chunk),
            )
            bars.append(bar)

        return bars

    def snapshot(self) -> DashboardSnapshot:
        return DashboardSnapshot(
            market_time=datetime.now(IST),
            feed_health=self.feed_health,
            signals=self.signals[:30],
            open_positions=list(self.trader.open_positions.values()),
            closed_trades=self.trader.closed_trades[-50:],
            equity_curve=self.equity_curve[-120:],
            market_data_history={s: list(h) for s, h in self.market_data_history.items()},
            latest_indicators=self.latest_indicators,
            metrics=self.trader.metrics(),
            kill_switch_enabled=self.trader.kill_switch_enabled,
        )

    def reset_runtime_state(self, keep_candles: bool = False) -> None:
        if not keep_candles:
            for candles in self.candles.values():
                candles.clear()
        self.latest_indicators.clear()
        self.signals.clear()
        self.equity_curve.clear()
        self.trader.reset()
        self.futures_trader.reset()

    async def kill_switch(self) -> DashboardSnapshot:
        prices = {symbol: candles[-1].close for symbol, candles in self.candles.items() if candles}
        await self.trader.enable_kill_switch(prices, datetime.now(IST))
        return self.snapshot()

    def reconstruct_recovered_option_legs(self) -> None:
        """
        Reconstruct option legs for any recovered active trades if their legs are empty.
        Uses the trade's symbol format, underlying_entry_price, and DerivativeManager.
        """
        if not hasattr(self, "dm") or not self.dm:
            return
            
        print("DEBUG: Reconstructing recovered option legs...")
        for trade in list(self.trader.open_positions.values()):
            if getattr(trade, "legs", []):
                continue
                
            # If symbol matches multi-leg structure format, e.g. "IRON_CONDOR:NIFTY_23JUN26"
            if ":" not in trade.instrument:
                continue
                
            strategy_name, suffix = trade.instrument.split(":", 1)
            underlying = "BANKNIFTY" if "BANKNIFTY" in suffix.upper() else "NIFTY"
            
            spot = trade.underlying_entry_price
            if not spot or spot <= 0:
                # Fallback to current spot if underlying entry price was not saved
                if underlying in self.candles and self.candles[underlying]:
                    spot = self.candles[underlying][-1].close
                else:
                    spot = 0
                    
            if spot <= 0:
                print(f"⚠️ Cannot reconstruct legs for {trade.instrument}: spot is 0")
                continue
                
            from services.chartedge_core.structures import select_structure
            from services.chartedge_core.models import LegExecution
            
            opt_dir = "CE" if trade.direction == Direction.BUY else "PE"
            
            # Reconstruct select_structure using defaults
            struct = select_structure(
                regime="RANGE_BOUND_CHOP", # generic fallback
                direction=opt_dir,
                iv_rank=50.0,
                spot=spot,
                optimal_strategy=strategy_name,
                strike_offset_config=int(self.config.risk.get("options_strike_offset", 0))
            )
            
            if not struct.trade or not struct.legs:
                print(f"⚠️ Failed to get structure definition for {strategy_name}")
                continue
                
            current_dt = trade.entry_time
            expiry_buffer = self.config.risk.get("options_expiry_buffer_days", 1)
            
            resolved_legs = []
            for leg in struct.legs:
                try:
                    options = self.dm.get_atm_options(
                        spot, 
                        underlying, 
                        current_dt=current_dt, 
                        expiry_buffer_days=expiry_buffer, 
                        strike_offset=leg.strike_offset
                    )
                    contract_data = options.get(leg.option_type)
                    if not contract_data:
                        continue
                        
                    token_id = contract_data["token"].split(":", 1)[1] if ":" in contract_data["token"] else contract_data["token"]
                    
                    resolved_legs.append(LegExecution(
                        instrument=contract_data["token"],
                        action=Direction.BUY if leg.action == "BUY" else Direction.SELL,
                        ratio=leg.ratio,
                        entry_price=contract_data.get("ltp", 0.0), # fallback, entry_price of leg isn't crucial for PnL of net premium
                        strike=contract_data.get("strike", 0.0),
                        option_type=leg.option_type
                    ))
                except Exception as ex:
                    print(f"⚠️ Error resolving leg for {trade.instrument}: {ex}")
                    
            if len(resolved_legs) == len(struct.legs):
                trade.legs = resolved_legs
                print(f"✅ Reconstructed {len(resolved_legs)} legs for recovered trade {trade.instrument}:")
                for leg in trade.legs:
                    print(f"  - {leg.action.value} {leg.instrument} @ strike {leg.strike}")
            else:
                print(f"⚠️ Leg count mismatch for {trade.instrument} during reconstruction")

    def _append_equity(self, now: datetime) -> None:
        metrics = self.trader.metrics()
        equity = metrics["realized_pnl"] + metrics["open_pnl"]
        self.equity_curve.append({"time": now.isoformat(), "equity": equity})

    def _make_candle(self, symbol: str, at: datetime, close: float) -> Candle:
        spread = max(close * 0.001, 8)
        open_price = close + random.uniform(-spread / 2, spread / 2)
        high = max(open_price, close) + random.uniform(0, spread)
        low = min(open_price, close) - random.uniform(0, spread)
        return Candle(
            time=at,
            instrument=symbol,
            timeframe="1m",
            open=round(open_price, 2),
            high=round(high, 2),
            low=round(low, 2),
            close=round(close, 2),
            volume=random.randint(80_000, 420_000),
        )

    def _get_market_context(self) -> "MarketContext":
        from services.chartedge_core.models import MarketContext
        
        vix = self.candles.get("INDIAVIX")
        vix_val = vix[-1].close if vix else 0.0
        
        rel = self.candles.get("RELIANCE")
        hdfc = self.candles.get("HDFCBANK")
        
        # Basis = Nifty Spot - Nifty Future (Mocking as 0 for now as we only track spot)
        return MarketContext(
            reliance_trend=self._calculate_trend("RELIANCE"),
            hdfc_bank_trend=self._calculate_trend("HDFCBANK"),
            india_vix=vix_val,
            gift_nifty_trend="STABLE", # Placeholder
            basis=0.0
        )

    def _get_options_data(self, symbol: str) -> Optional["OptionChainData"]:
        # Only for Indices in this institutional desk
        if symbol not in ["NIFTY", "BANKNIFTY"]:
            return None
        return None # Overridden in live runtime

    def _calculate_trend(self, symbol: str) -> str:
        candles = self.candles.get(symbol)
        if not candles or len(candles) < 5:
            return "NEUTRAL"
        
        # % change over last 15 mins (or whatever we have)
        lookback = min(15, len(candles))
        start_price = candles[-lookback].close
        current_price = candles[-1].close
        
        if start_price == 0:
            return "NEUTRAL"
        
        change_pct = (current_price - start_price) / start_price * 100
        if change_pct > 0.15: return "BULLISH"
        if change_pct < -0.15: return "BEARISH"
        return "NEUTRAL"

    def _higher_timeframe(self, symbol: str) -> dict[str, str]:
        candles = list(self.candles[symbol])
        if not candles:
            return {"1hr": "NEUTRAL", "1D": "NEUTRAL"}
            
        buckets: dict[str, list[Candle]] = defaultdict(list)
        buckets["1hr"] = candles[-60:]
        buckets["1D"] = candles[-180:]
        result = {}
        for frame, values in buckets.items():
            if len(values) < 2:
                result[frame] = "NEUTRAL"
            else:
                result[frame] = "UP" if values[-1].close > values[0].close else "DOWN"
        return result

    def get_combined_daily_drawdown_pct(self, current_date: date) -> float:
        total_capital = self.config.risk.get("total_capital", 200000.0)
        
        # Options daily PnL
        opt_realized = sum(t.pnl for t in self.trader.closed_trades if t.exit_time and t.exit_time.date() == current_date)
        opt_open = sum(t.pnl for t in self.trader.open_positions.values())
        
        # Futures daily PnL
        fut_realized = sum(t.pnl for t in self.futures_trader.closed_trades if t.exit_time and t.exit_time.date() == current_date)
        fut_open = sum(t.pnl for t in self.futures_trader.open_positions.values())
        
        combined_pnl = opt_realized + opt_open + fut_realized + fut_open
        dd_pct = (combined_pnl / total_capital) * 100
        return round(dd_pct, 2)

    async def trigger_global_kill_switch(self, now: datetime, ltp_map: dict[str, float] = None, candle: Candle = None) -> None:
        print(f"🛑 [MarketSimulator] GLOBAL KILL SWITCH TRIGGERED at {now}!")
        
        # 1. Close options positions
        prices = {}
        for sym, t in self.trader.open_positions.items():
            if ltp_map and sym in ltp_map:
                prices[sym] = ltp_map[sym]
            elif candle and sym == candle.instrument:
                prices[sym] = candle.close
            else:
                prices[sym] = t.entry_price
        await self.trader.enable_kill_switch(prices, now)
        
        # 2. Close futures positions
        fut_prices = {}
        for sym, t in self.futures_trader.open_positions.items():
            if candle and sym.startswith(candle.instrument):
                fut_prices[sym] = candle.close
            else:
                fut_prices[sym] = t.entry_price
        
        self.futures_trader.kill_switch = True
        await self.futures_trader.force_close_all(fut_prices, now, "KILL_SWITCH")

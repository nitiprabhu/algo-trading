from __future__ import annotations

import asyncio
import math
import random
from collections import defaultdict, deque
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import uuid
from services.chartedge_core.ai_signal import SignalEngine
from services.chartedge_core.config import Config
from services.chartedge_core.confluence import score
from services.chartedge_core.indicators import compute_snapshot_indicators
from services.chartedge_core.models import Candle, DashboardSnapshot, Direction, IndicatorSnapshot, Signal
from services.chartedge_core.paper_trading import PaperTradingEngine
from services.chartedge_core.training_logger import training_logger, options_logger


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
        self.trader = PaperTradingEngine(config.risk, skip_db_load=skip_db_load)
        self.signal_engine = SignalEngine(config.ai, config.confluence_thresholds)
        self.last_signal_times: dict[tuple[str, str], datetime] = {}
        self._running = False
        self.is_seeding = False
        self.is_backtesting = False

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

    async def process_closed_candle(self, candle: Candle) -> None:
        if candle.time.minute % 30 == 0:
            print(f"DEBUG: Processing candles at {candle.time}...")
        symbol = candle.instrument
        self.candles[symbol].append(candle)
        self.market_data_history[symbol].append({"time": candle.time.isoformat(), "price": candle.close})
        
        # Only trading symbols can have positions and signals
        if symbol in self.config.trading_symbols:
            snapshot = self.latest_indicators.get(symbol)
            await self.trader.mark_to_market(candle, snapshot, ltp_map=self._token_ltp)
            
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
            fo_signal = await self.signal_engine.get_fo_signal(candle, list(self.candles[symbol]), vix_val, snapshot)
            if fo_signal:
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
        )
        self.latest_indicators[symbol] = snapshot
        training_logger.log_snapshot(snapshot)
        options_logger.log_options_state(snapshot)
        
        signal = await self.signal_engine.generate(snapshot, candles_agg)
        
        if signal.signal != Direction.HOLD:
             print(f"📡 SIGNAL ({symbol}): {signal.signal} Confidence: {signal.confidence}%")
        
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

        # OPTIONS PROXY LOGIC: Check for ATM option contract if it's a trading index
        if symbol in ["NIFTY", "BANKNIFTY"] and signal.signal != Direction.HOLD:
            # If the signal has an explicit option_type (CE/PE), use it. Otherwise fallback to sentiment.
            preferred_side = signal.option_type if signal.option_type else signal.signal.value
            opt_contract = self.get_option_contract(symbol, preferred_side)
            
            # Resolve premium: Use LTP if available, fallback to proxy price (~1.2% of spot) for seeding/backtests
            premium = opt_contract.get("ltp", 0) if opt_contract else 0
            if premium <= 0:
                # Institutional Proxy: ATM Options usually trade around 1.0-1.5% of spot
                premium = round(candle.close * 0.012, 2)
            
            if opt_contract:
                print(f"🎯 Creating Option Trade for {symbol}: {opt_contract['symbol']} @ {premium}")
                # Clone signal and next_open for the option
                opt_signal = signal.model_copy()
                opt_signal.id = uuid.uuid4() # Generate NEW unique ID for the option signal
                opt_signal.instrument = opt_contract["symbol"]
                opt_signal.signal = Direction.BUY  # We always BUY the option contract (long CE/PE) to profit from underlying momentum
                
                # PRD: Translate Index SL/Targets to Option Premium Levels (Delta Proxy = 0.5)
                delta = 0.5
                # For CE: price moves with underlying. For PE: price moves opposite.
                is_pe = "-PE" in opt_signal.instrument or "_PE" in opt_signal.instrument
                if is_pe:
                     opt_signal.stop_loss = round(premium + (signal.stop_loss - candle.close) * -delta, 2)
                     opt_signal.target_1 = round(premium + (signal.target_1 - candle.close) * -delta, 2)
                     opt_signal.target_2 = round(premium + (signal.target_2 - candle.close) * -delta, 2)
                else:
                     opt_signal.stop_loss = round(premium + (signal.stop_loss - candle.close) * delta, 2)
                     opt_signal.target_1 = round(premium + (signal.target_1 - candle.close) * delta, 2)
                     opt_signal.target_2 = round(premium + (signal.target_2 - candle.close) * delta, 2)
                
                print(f"🎯 Translated Option Levels for {opt_signal.instrument}: SL={opt_signal.stop_loss}, T1={opt_signal.target_1}, T2={opt_signal.target_2}")
                
                from services.chartedge_core.models import EntryZone
                opt_signal.entry_zone = EntryZone(low=round(premium * 0.98, 2), high=round(premium * 1.02, 2))
                opt_signal.reasoning = f"Option Proxy for {symbol}: {signal.reasoning}"
                
                opt_open = next_open.model_copy()
                opt_open.instrument = opt_contract["symbol"]
                opt_open.open = premium
                
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

    async def kill_switch(self) -> DashboardSnapshot:
        prices = {symbol: candles[-1].close for symbol, candles in self.candles.items() if candles}
        await self.trader.enable_kill_switch(prices, datetime.now(IST))
        return self.snapshot()

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

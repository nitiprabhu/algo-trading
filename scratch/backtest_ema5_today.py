import asyncio
import os
import yaml
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime
from services.chartedge_core.strategies import OptionStrategy
from services.chartedge_core.models import Candle, Direction, EntryZone, IndicatorSnapshot, Signal
from services.chartedge_core.indicators import ema

IST = ZoneInfo("Asia/Kolkata")

class FixedFiveEMAScalping(OptionStrategy):
    def __init__(self, timeframe_minutes: int = 5):
        self.timeframe = timeframe_minutes
        self.alert_candle = None
        self.ema5 = 0.0

    def _aggregate_to_5m(self, candles: list[Candle]) -> list[Candle]:
        if not candles:
            return []
        grouped = {}
        for c in candles:
            minute = c.time.minute
            rounded_minute = (minute // 5) * 5
            bar_time = c.time.replace(minute=rounded_minute, second=0, microsecond=0)
            if bar_time not in grouped:
                grouped[bar_time] = []
            grouped[bar_time].append(c)
            
        bars = []
        for bar_time in sorted(grouped.keys()):
            chunk = grouped[bar_time]
            bar = Candle(
                time=bar_time,
                instrument=chunk[0].instrument,
                timeframe="5m",
                open=chunk[0].open,
                high=max(x.high for x in chunk),
                low=min(x.low for x in chunk),
                close=chunk[-1].close,
                volume=sum(x.volume for x in chunk)
            )
            bars.append(bar)
        return bars

    def _get_completed_bars(self, candles: list[Candle], current_time: datetime) -> list[Candle]:
        bars_5m = self._aggregate_to_5m(candles)
        completed = [b for b in bars_5m if current_time >= b.time + timedelta(minutes=5)]
        return completed

    def update(self, candle: Candle, candles: list[Candle]):
        completed_bars = self._get_completed_bars(candles, candle.time)
        if len(completed_bars) < 5:
            return
            
        closes_5m = [b.close for b in completed_bars]
        self.ema5 = ema(closes_5m, 5)
        
        last_completed = completed_bars[-1]
        
        # Reset alert if instrument changes
        if self.alert_candle and self.alert_candle.instrument != candle.instrument:
            self.alert_candle = None
            
        # Alert Candle Detection (PE entry setup)
        # Price range including low is completely above 5 EMA
        if last_completed.low > self.ema5:
            self.alert_candle = last_completed
        # Alert Candle Detection (CE entry setup)
        elif last_completed.high < self.ema5:
            self.alert_candle = last_completed
        else:
            self.alert_candle = None

    def get_signal(self, candle: Candle, india_vix: float = 0.0) -> dict | None:
        if not self.alert_candle:
            return None

        if india_vix > 22.0:
            return None

        # Check if the current 1-minute candle touches the 5 EMA (invalidates the setup)
        if candle.low <= self.ema5 <= candle.high:
            self.alert_candle = None
            return None

        # PE Trigger: Breach low of alert candle (completely above EMA)
        if self.alert_candle.low > self.ema5 and candle.close < self.alert_candle.low:
            res = {
                "strategy": "5EMA",
                "direction": "BUY",
                "option_type": "PE",
                "reason": f"5EMA Scalp: Price breached Alert Candle Low (VIX: {india_vix})",
                "sl": self.alert_candle.high
            }
            self.alert_candle = None  # Reset after trigger
            return res
        
        # CE Trigger: Breach high of alert candle (completely below EMA)
        if self.alert_candle.high < self.ema5 and candle.close > self.alert_candle.high:
            res = {
                "strategy": "5EMA",
                "direction": "BUY",
                "option_type": "CE",
                "reason": f"5EMA Scalp: Price breached Alert Candle High (VIX: {india_vix})",
                "sl": self.alert_candle.low
            }
            self.alert_candle = None  # Reset after trigger
            return res
            
        return None

async def test_fixed_strategy():
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)
    
    # Enable rule-based to isolate strategy logic
    config.ai["enabled"] = False
    
    # We will override the SignalEngine's get_fo_signal function to use our FixedFiveEMAScalping
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.trader.is_backtesting = True
    
    # Set threshold high to ensure we only get F&O strategy signals
    for key in config.confluence_thresholds:
        config.confluence_thresholds[key] = 0.99
    config.confluence_thresholds["DEFAULT"] = 0.99
    
    # Override get_fo_signal
    original_get_fo_signal = runtime.signal_engine.get_fo_signal
    
    async def custom_get_fo_signal(candle, candles, india_vix=0.0, latest_snapshot=None):
        symbol = candle.instrument
        if symbol not in runtime.signal_engine.strategies:
            runtime.signal_engine.strategies[symbol] = {
                "t315": runtime.signal_engine.strategies.get(symbol, {}).get("t315") or runtime.signal_engine.strategies.get(symbol, {}).get("t315", None) or runtime.signal_engine.strategies.get(symbol, {}).get("t315") or None, # wait
            }
            # We want to replace ema5 with our FixedFiveEMAScalping
            from services.chartedge_core.strategies import EagleNiftyT315
            runtime.signal_engine.strategies[symbol] = {
                "t315": EagleNiftyT315(),
                "ema5": FixedFiveEMAScalping()
            }
            
        strategies = runtime.signal_engine.strategies[symbol]
        
        # 1. Check trigger for 5EMA using the state before update
        ema5_trigger = strategies["ema5"].get_signal(candle, india_vix)
        
        # 2. Update states
        strategies["t315"].update(candle)
        strategies["ema5"].update(candle, candles)
        
        # 3. Check trigger for T315
        t315_trigger = strategies["t315"].get_signal(candle, india_vix)
        
        trigger = t315_trigger or ema5_trigger
        if not trigger:
            return None
            
        base_signal = runtime.signal_engine._from_strategy_dict(trigger, candle)
        return base_signal

    runtime.signal_engine.get_fo_signal = custom_get_fo_signal
    
    start = datetime(2026, 5, 22, 9, 15, tzinfo=IST)
    end = datetime(2026, 5, 22, 15, 30, tzinfo=IST)
    
    print("Running backtest for May 22, 2026 with FIXED 5 EMA strategy...")
    await runtime.run_backtest(start, end)
    
    print("\n=== CLOSED TRADES ===")
    for t in runtime.trader.closed_trades:
        matching_sig = next((s for s in runtime.signals if s.id == t.signal_id), None)
        strat = matching_sig.strategy_name if matching_sig else "UNKNOWN"
        exit_pr = t.exit_price if t.exit_price else 0.0
        print(f"Trade: {t.instrument} | Strategy: {strat} | Entry: {t.entry_time.strftime('%H:%M')} ({t.entry_price:.2f}) | Exit: {t.exit_time.strftime('%H:%M') if t.exit_time else 'N/A'} ({exit_pr:.2f}) | PnL: {t.pnl:+.2f} ({t.pnl_pct:+.2f}%)")

if __name__ == "__main__":
    asyncio.run(test_fixed_strategy())

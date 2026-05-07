from __future__ import annotations

from datetime import datetime, time
from typing import Optional, Dict

from services.chartedge_core.models import Candle, Direction
from services.chartedge_core.indicators import ema

class OptionStrategy:
    def update(self, candle: Candle):
        pass

    def get_signal(self, candle: Candle, india_vix: float = 0.0) -> Optional[Dict]:
        pass

class EagleNiftyT315(OptionStrategy):
    """
    Eagle Nifty T315 Breakout Protocol
    1. Time-Bound Range: 09:15 - 09:30 AM (15-min candle).
    2. Breakout Trigger: LTP breaches 15-min High (CE) or Low (PE).
    3. Retracement Validation: Monitors for structural support/retracement after breakout.
    4. Volatility Filter: INDIA VIX must be between 14 and 18.
    """
    def __init__(self):
        self.range_high: Optional[float] = None
        self.range_low: Optional[float] = None
        self.is_range_set = False
        self.last_signal_time: Optional[datetime] = None
        self.breakout_detected: Optional[str] = None  # "CE" or "PE"
        self.breakout_price: Optional[float] = None

    def update(self, candle: Candle):
        if candle.instrument != "NIFTY":
            return

        # Time-Bound Range Definition (09:15 - 09:30 IST)
        t = candle.time.time()
        if time(9, 15) <= t <= time(9, 30):
            if self.range_high is None or candle.high > self.range_high:
                self.range_high = candle.high
            if self.range_low is None or candle.low < self.range_low:
                self.range_low = candle.low
        
        if t > time(9, 30):
            if not self.is_range_set and self.range_high is not None:
                print(f"🎯 T315 Range Set: {self.range_low} - {self.range_high}")
                self.is_range_set = True

    def get_signal(self, candle: Candle, india_vix: float = 0.0) -> Optional[Dict]:
        if not self.is_range_set or self.range_high is None or self.range_low is None or candle.instrument != "NIFTY":
            return None
        
        # Ensure we only signal once per day
        if self.last_signal_time and self.last_signal_time.date() == candle.time.date():
            return None

        # 4. Volatility Filter: India VIX between 14 and 22
        if not (14.0 <= india_vix <= 22.0):
            return None

        # 2. Breakout Trigger Mechanism
        if not self.breakout_detected:
            if candle.close > self.range_high:
                self.breakout_detected = "CE"
                self.breakout_price = candle.close
                print(f"🔥 T315 Breakout Detected: CE above {self.range_high} at {candle.time}")
            elif candle.close < self.range_low:
                self.breakout_detected = "PE"
                self.breakout_price = candle.close
                print(f"🔥 T315 Breakout Detected: PE below {self.range_low} at {candle.time}")
            return None

        # 3. Retracement and Validation (PRD variation)
        # We look for price to hold above/below the breakout level for confirmation
        if self.breakout_detected == "CE":
            if candle.close > self.range_high:
                print(f"✅ T315 CE Validated at {candle.time}")
                self.last_signal_time = candle.time
                return {
                    "strategy": "T315",
                    "direction": "BUY",
                    "option_type": "CE",
                    "reason": f"NIFTY T315 Breakout + Validation (VIX: {india_vix})",
                    "sl": self.range_low
                }
            else:
                print(f"❌ T315 CE Validation Failed (price {candle.close} fell back) at {candle.time}")
                self.breakout_detected = None
        
        elif self.breakout_detected == "PE":
            if candle.close < self.range_low:
                print(f"✅ T315 PE Validated at {candle.time}")
                self.last_signal_time = candle.time
                return {
                    "strategy": "T315",
                    "direction": "BUY",
                    "option_type": "PE",
                    "reason": f"NIFTY T315 Breakout + Validation (VIX: {india_vix})",
                    "sl": self.range_high
                }
            else:
                print(f"❌ T315 PE Validation Failed (price {candle.close} fell back) at {candle.time}")
                self.breakout_detected = None
        
        return None

class FiveEMAScalping(OptionStrategy):
    """
    5 EMA High-Frequency Scalping
    1. Alert Candle: Completely detached from 5 EMA
    2. Trigger: Breach of alert candle's low (for PE) or high (for CE)
    """
    def __init__(self, timeframe_minutes: int = 5):
        self.timeframe = timeframe_minutes
        self.alert_candle: Optional[Candle] = None
        self.ema5: float = 0.0

    def update(self, candle: Candle, candles: list[Candle]):
        if len(candles) < 5:
            return
        
        closes = [c.close for c in candles]
        self.ema5 = ema(closes, 5)

        # Reset alert if instrument changes (should not happen with per-instrument instances, but good guardrail)
        if self.alert_candle and self.alert_candle.instrument != candle.instrument:
            self.alert_candle = None

        # Alert Candle Detection (PE entry setup)
        # Price range including low is completely above 5 EMA
        if candle.low > self.ema5:
            self.alert_candle = candle
        # Alert Candle Detection (CE entry setup - inverse logic)
        elif candle.high < self.ema5:
            self.alert_candle = candle
        else:
            # If candle touches EMA, setup invalidates
            self.alert_candle = None

    def get_signal(self, candle: Candle, india_vix: float = 0.0) -> Optional[Dict]:
        if not self.alert_candle:
            return None

        # Volatility Filter: Scalping needs some volatility, but avoid extreme zones
        if india_vix > 22.0:
            return None

        # PE Trigger: Breach low of alert candle that was above EMA
        if self.alert_candle.low > self.ema5 and candle.close < self.alert_candle.low:
            res = {
                "strategy": "5EMA",
                "direction": "BUY",
                "option_type": "PE",
                "reason": f"5EMA Scalp: Price breached Alert Candle Low (VIX: {india_vix})",
                "sl": self.alert_candle.high
            }
            self.alert_candle = None # Reset after trigger
            return res
        
        # CE Trigger: Breach high of alert candle that was below EMA
        if self.alert_candle.high < self.ema5 and candle.close > self.alert_candle.high:
            res = {
                "strategy": "5EMA",
                "direction": "BUY",
                "option_type": "CE",
                "reason": f"5EMA Scalp: Price breached Alert Candle High (VIX: {india_vix})",
                "sl": self.alert_candle.low
            }
            self.alert_candle = None # Reset after trigger
            return res
            
        # Reset alert if price touches EMA (setup invalidates)
        if (candle.low <= self.ema5 <= candle.high):
            self.alert_candle = None

        return None

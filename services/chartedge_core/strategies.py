from __future__ import annotations

from datetime import datetime, time, date, timedelta
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
    Eagle T315 Breakout Protocol (NIFTY + BANKNIFTY)
    Opening Range: 09:15–09:45 (30-min ORB — reduces noise vs 15-min).
    Breakout Trigger: 1-min close breaches 30-min High (CE) or Low (PE).
    Validation: 3 consecutive 1-min closes beyond breakout level.
    Volume: Breakout candle volume > 1.5x trailing 10-candle average.
    Body filter: Breakout candle body > 40% of full range (no wick-only breaks).
    Entry cutoff: No new entries after 10:15 (theta math).
    VIX: 14–22.
    """
    SUPPORTED_INSTRUMENTS = ("NIFTY", "BANKNIFTY")
    RANGE_END = time(9, 45)
    ENTRY_CUTOFF = time(10, 15)
    VALIDATION_CANDLES = 2
    VOLUME_MULTIPLIER = 1.5
    BODY_RATIO_MIN = 0.40

    def __init__(self):
        self._state: dict[str, dict] = {}

    def _get_state(self, symbol: str) -> dict:
        if symbol not in self._state:
            self._state[symbol] = {
                "range_high": None,
                "range_low": None,
                "is_range_set": False,
                "breakout_detected": None,
                "breakout_price": None,
                "validation_count": 0,
                "breakout_volume": 0,
                "recent_volumes": [],
                "current_day": None,
                "last_signal_time": None,
            }
        return self._state[symbol]

    def update(self, candle: Candle):
        if candle.instrument not in self.SUPPORTED_INSTRUMENTS:
            return

        s = self._get_state(candle.instrument)
        current_date = candle.time.date()
        if s["current_day"] is None or s["current_day"] != current_date:
            s["current_day"] = current_date
            s["range_high"] = None
            s["range_low"] = None
            s["is_range_set"] = False
            s["breakout_detected"] = None
            s["breakout_price"] = None
            s["validation_count"] = 0
            s["breakout_volume"] = 0
            s["recent_volumes"] = []

        t = candle.time.time()

        # Build rolling volume history (pre-range candles for avg)
        if t < self.RANGE_END:
            s["recent_volumes"].append(candle.volume)
            if len(s["recent_volumes"]) > 10:
                s["recent_volumes"].pop(0)

        # Build 30-min opening range
        if time(9, 15) <= t <= self.RANGE_END:
            if s["range_high"] is None or candle.high > s["range_high"]:
                s["range_high"] = candle.high
            if s["range_low"] is None or candle.low < s["range_low"]:
                s["range_low"] = candle.low

        if t > self.RANGE_END and not s["is_range_set"] and s["range_high"] is not None:
            print(f"🎯 T315 Range Set [{candle.instrument}] for {current_date}: {s['range_low']} - {s['range_high']}")
            s["is_range_set"] = True

    def get_signal(self, candle: Candle, india_vix: float = 0.0, vix_band: Optional[tuple[float, float]] = None) -> Optional[Dict]:
        if candle.instrument not in self.SUPPORTED_INSTRUMENTS:
            return None

        s = self._get_state(candle.instrument)
        if not s["is_range_set"] or s["range_high"] is None or s["range_low"] is None:
            return None

        if s["last_signal_time"] and s["last_signal_time"].date() == candle.time.date():
            return None

        t = candle.time.time()
        if t >= self.ENTRY_CUTOFF:
            return None

        vix_lower, vix_upper = vix_band if vix_band else (14.0, 22.0)
        if not (vix_lower <= india_vix <= vix_upper):
            return None

        if not s["breakout_detected"]:
            # Body filter: close must be in directional half of candle range
            candle_range = candle.high - candle.low or 0.01
            body = abs(candle.close - candle.open)
            body_ratio = body / candle_range

            if candle.close > s["range_high"]:
                if body_ratio < self.BODY_RATIO_MIN:
                    return None  # wick breakout, skip
                # Volume filter
                avg_vol = sum(s["recent_volumes"]) / len(s["recent_volumes"]) if s["recent_volumes"] else 0
                if avg_vol > 0 and candle.volume < avg_vol * self.VOLUME_MULTIPLIER:
                    print(f"⚡ T315 CE breakout volume too low ({candle.volume} < {avg_vol * self.VOLUME_MULTIPLIER:.0f})")
                    return None
                s["breakout_detected"] = "CE"
                s["breakout_price"] = candle.close
                s["validation_count"] = 1
                s["breakout_volume"] = candle.volume
                print(f"🔥 T315 Breakout [{candle.instrument}]: CE above {s['range_high']} at {candle.time} (body={body_ratio:.0%} vol={candle.volume})")
            elif candle.close < s["range_low"]:
                if body_ratio < self.BODY_RATIO_MIN:
                    return None
                avg_vol = sum(s["recent_volumes"]) / len(s["recent_volumes"]) if s["recent_volumes"] else 0
                if avg_vol > 0 and candle.volume < avg_vol * self.VOLUME_MULTIPLIER:
                    print(f"⚡ T315 PE breakout volume too low ({candle.volume} < {avg_vol * self.VOLUME_MULTIPLIER:.0f})")
                    return None
                s["breakout_detected"] = "PE"
                s["breakout_price"] = candle.close
                s["validation_count"] = 1
                s["breakout_volume"] = candle.volume
                print(f"🔥 T315 Breakout [{candle.instrument}]: PE below {s['range_low']} at {candle.time} (body={body_ratio:.0%} vol={candle.volume})")
            return None

        # Validation phase: require VALIDATION_CANDLES consecutive closes beyond level
        if s["breakout_detected"] == "CE":
            if candle.close > s["range_high"]:
                s["validation_count"] += 1
                if s["validation_count"] >= self.VALIDATION_CANDLES:
                    print(f"✅ T315 CE Validated [{candle.instrument}] at {candle.time} ({s['validation_count']} candles)")
                    s["last_signal_time"] = candle.time
                    return {
                        "strategy": "T315",
                        "direction": "BUY",
                        "option_type": "CE",
                        "reason": f"{candle.instrument} T315 30m-ORB Breakout ({s['validation_count']}x validated, VIX: {india_vix:.1f})",
                        "sl": s["range_low"],
                    }
            else:
                print(f"❌ T315 CE Validation Failed [{candle.instrument}] at {candle.time} (count was {s['validation_count']})")
                s["breakout_detected"] = None
                s["validation_count"] = 0

        elif s["breakout_detected"] == "PE":
            if candle.close < s["range_low"]:
                s["validation_count"] += 1
                if s["validation_count"] >= self.VALIDATION_CANDLES:
                    print(f"✅ T315 PE Validated [{candle.instrument}] at {candle.time} ({s['validation_count']} candles)")
                    s["last_signal_time"] = candle.time
                    return {
                        "strategy": "T315",
                        "direction": "BUY",
                        "option_type": "PE",
                        "reason": f"{candle.instrument} T315 30m-ORB Breakout ({s['validation_count']}x validated, VIX: {india_vix:.1f})",
                        "sl": s["range_high"],
                    }
            else:
                print(f"❌ T315 PE Validation Failed [{candle.instrument}] at {candle.time} (count was {s['validation_count']})")
                s["breakout_detected"] = None
                s["validation_count"] = 0

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
        self.ema9_5m: float = 0.0
        self.ema21_5m: float = 0.0

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
        if len(closes_5m) >= 21:
            self.ema9_5m = ema(closes_5m, 9)
            self.ema21_5m = ema(closes_5m, 21)
        else:
            self.ema9_5m = 0.0
            self.ema21_5m = 0.0
        
        last_completed = completed_bars[-1]
        
        # Reset alert if instrument changes
        if self.alert_candle and self.alert_candle.instrument != candle.instrument:
            self.alert_candle = None
            
        # Alert Candle Detection: persist until EMA is touched
        if last_completed.low > self.ema5:
            # Fully above EMA — PE setup alert
            self.alert_candle = last_completed
        elif last_completed.high < self.ema5:
            # Fully below EMA — CE setup alert
            self.alert_candle = last_completed
        elif self.alert_candle is not None:
            # Bar overlaps EMA — setup invalidated; otherwise keep existing alert
            if last_completed.low <= self.ema5 <= last_completed.high:
                self.alert_candle = None

    def get_signal(self, candle: Candle, india_vix: float = 0.0, latest_snapshot: Optional[IndicatorSnapshot] = None, vix_band: Optional[tuple[float, float]] = None) -> Optional[Dict]:
        if not self.alert_candle:
            return None

        # Time Filter: Skip price-discovery open and afternoon theta-burn zone
        t = candle.time.time()
        if t < time(9, 50) or t >= time(13, 0):
            return None

        # Volatility Filter: Scalping needs some volatility, but avoid extreme zones
        vix_lower, vix_upper = vix_band if vix_band else (10.0, 22.0)
        if india_vix > vix_upper:
            return None

        # Trend Strength Filter: Do not take counter-trend trades if trend is strong
        if self.ema9_5m and self.ema21_5m:
            gap = (self.ema9_5m - self.ema21_5m) / self.ema21_5m
            if gap > 0.0010: # > 0.1% trend strength (bullish)
                if self.alert_candle.low > self.ema5:
                    print(f"🚫 [Filter] 5EMA PE Blocked: Strong 5m Uptrend (gap: {gap:.4f})")
                    return None
            elif gap < -0.0010: # < -0.1% trend strength (bearish)
                if self.alert_candle.high < self.ema5:
                    print(f"🚫 [Filter] 5EMA CE Blocked: Strong 5m Downtrend (gap: {gap:.4f})")
                    return None

        # 1m Ribbon Trend Filter
        if latest_snapshot and "ema_ribbon" in latest_snapshot.indicators:
            ema_vote = latest_snapshot.indicators["ema_ribbon"].vote
            if ema_vote == 1 and self.alert_candle.low > self.ema5:
                print(f"🚫 [Filter] 5EMA PE Blocked: Bullish 1m EMA Ribbon")
                return None
            if ema_vote == -1 and self.alert_candle.high < self.ema5:
                print(f"🚫 [Filter] 5EMA CE Blocked: Bearish 1m EMA Ribbon")
                return None

        # Reset alert if price touches EMA (setup invalidates)
        if candle.low <= self.ema5 <= candle.high:
            self.alert_candle = None
            return None

        # PE Trigger: Breach low of alert candle that was above EMA
        if self.alert_candle.low > self.ema5 and candle.close < self.alert_candle.low:
            if candle.time.minute % 5 == 4: # 5-minute candle close confirmation
                res = {
                    "strategy": "5EMA",
                    "direction": "BUY",
                    "option_type": "PE",
                    "reason": f"5EMA Scalp: Price breached Alert Candle Low with 5m confirm (VIX: {india_vix})",
                    "sl": self.alert_candle.high
                }
                self.alert_candle = None  # Reset after trigger
                return res
        
        # CE Trigger: Breach high of alert candle that was below EMA
        if self.alert_candle.high < self.ema5 and candle.close > self.alert_candle.high:
            if candle.time.minute % 5 == 4: # 5-minute candle close confirmation
                res = {
                    "strategy": "5EMA",
                    "direction": "BUY",
                    "option_type": "CE",
                    "reason": f"5EMA Scalp: Price breached Alert Candle High with 5m confirm (VIX: {india_vix})",
                    "sl": self.alert_candle.low
                }
                self.alert_candle = None  # Reset after trigger
                return res
            
        return None


class NiftyFuturesORB(OptionStrategy):
    """
    Nifty Futures Opening Range Breakout (ORB)
    ──────────────────────────────────────────
    Session 1 — ORB (09:15–09:45):
        Build the 30-min opening range. Breakout above range_high → BUY futures.
        Breakdown below range_low → SELL futures.

    Session 2 — Midday Trend (11:00–11:30):
        If no trade from session 1, check directional momentum using 15-min EMAs.
        EMA9 > EMA21 and price above VWAP → BUY. Vice versa → SELL.

    Session 3 — Close Setup (14:00–14:30):
        Final entry window. Only trade with ADX > 28 (strong trend) and VWAP-aligned.

    Common rules:
        - 1 trade per day (first signal wins)
        - Breakout candle body must be > 35% of range (no wick-only breaks)
        - Volume must be > 1.2× trailing 10-candle average
        - VIX filter: 10–28
    """
    SUPPORTED_INSTRUMENTS = ("NIFTY", "NIFTY_FUT")
    RANGE_END   = time(9, 45)
    ORB_CUTOFF  = time(10, 15)
    MID_START   = time(11, 0)
    MID_END     = time(11, 30)
    CLOSE_START = time(14, 0)
    CLOSE_END   = time(14, 30)
    BODY_MIN    = 0.35
    VOL_MULT    = 1.2
    VIX_MIN     = 10.0
    VIX_MAX     = 28.0

    def __init__(self):
        self._state: dict[str, dict] = {}

    def _get_state(self, symbol: str) -> dict:
        if symbol not in self._state:
            self._state[symbol] = {
                "range_high": None,
                "range_low": None,
                "is_range_set": False,
                "breakout_dir": None,
                "recent_volumes": [],
                "current_day": None,
                "traded_today": False,
                "ema9": 0.0,
                "ema21": 0.0,
                "closes_15m": [],
            }
        return self._state[symbol]

    def update(self, candle: Candle):
        # Accept NIFTY candles — futures price tracks spot closely
        if candle.instrument not in ("NIFTY", "NIFTY_FUT"):
            return

        s = self._get_state("NIFTY_FUT")
        current_date = candle.time.date()
        if s["current_day"] != current_date:
            s["current_day"]    = current_date
            s["range_high"]     = None
            s["range_low"]      = None
            s["is_range_set"]   = False
            s["breakout_dir"]   = None
            s["recent_volumes"] = []
            s["traded_today"]   = False
            s["closes_15m"]     = []

        t = candle.time.time()

        if t < self.RANGE_END:
            s["recent_volumes"].append(candle.volume)
            if len(s["recent_volumes"]) > 10:
                s["recent_volumes"].pop(0)

        if time(9, 15) <= t <= self.RANGE_END:
            if s["range_high"] is None or candle.high > s["range_high"]:
                s["range_high"] = candle.high
            if s["range_low"] is None or candle.low < s["range_low"]:
                s["range_low"] = candle.low

        if t > self.RANGE_END and not s["is_range_set"] and s["range_high"] is not None:
            s["is_range_set"] = True
            rng = s["range_high"] - s["range_low"]
            print(f"🎯 [FUT-ORB] Range set: {s['range_low']:.2f}–{s['range_high']:.2f} ({rng:.2f} pts)")

        if candle.time.minute % 15 == 0:
            s["closes_15m"].append(candle.close)
            if len(s["closes_15m"]) > 30:
                s["closes_15m"].pop(0)
            if len(s["closes_15m"]) >= 21:
                s["ema9"]  = ema(s["closes_15m"], 9)
                s["ema21"] = ema(s["closes_15m"], 21)

    def get_signal(
        self,
        candle: Candle,
        india_vix: float = 0.0,
        vwap: float = 0.0,
        adx: float = 0.0,
        vix_band: Optional[tuple[float, float]] = None,
    ) -> Optional[Dict]:
        s = self._get_state("NIFTY_FUT")

        if s["traded_today"]:
            return None

        vix_lo, vix_hi = vix_band if vix_band else (self.VIX_MIN, self.VIX_MAX)
        if india_vix > 0 and not (vix_lo <= india_vix <= vix_hi):
            return None

        t = candle.time.time()

        # ── Session 1: ORB ─────────────────────────────────────────────────────
        if s["is_range_set"] and self.RANGE_END < t <= self.ORB_CUTOFF:
            sig = self._check_orb(candle, s, india_vix)
            if sig:
                s["traded_today"] = True
                return sig

        # ── Session 2: Midday Trend ─────────────────────────────────────────────
        if self.MID_START <= t <= self.MID_END and s["ema9"] and s["ema21"]:
            sig = self._check_trend(candle, s, "MIDDAY", india_vix, vwap)
            if sig:
                s["traded_today"] = True
                return sig

        # ── Session 3: Close Setup ─────────────────────────────────────────────
        if self.CLOSE_START <= t <= self.CLOSE_END and adx >= 28:
            sig = self._check_trend(candle, s, "CLOSE", india_vix, vwap)
            if sig:
                s["traded_today"] = True
                return sig

        return None

    def _check_orb(self, candle: Candle, s: dict, vix: float) -> Optional[Dict]:
        if not s["is_range_set"] or s["breakout_dir"] is not None:
            return None
        candle_rng = (candle.high - candle.low) or 0.01
        body_ratio = abs(candle.close - candle.open) / candle_rng
        avg_vol = sum(s["recent_volumes"]) / len(s["recent_volumes"]) if s["recent_volumes"] else 0

        if candle.close > s["range_high"]:
            if body_ratio < self.BODY_MIN or (avg_vol > 0 and candle.volume < avg_vol * self.VOL_MULT):
                return None
            s["breakout_dir"] = "BUY"
            print(f"🔥 [FUT-ORB] BUY above {s['range_high']:.2f} at {candle.time}")
            return {
                "strategy": "FUT_ORB",
                "direction": "BUY",
                "option_type": None,
                "reason": f"Nifty Futures ORB BUY breakout above {s['range_high']:.2f} (VIX={vix:.1f})",
                "sl": s["range_low"],
            }

        if candle.close < s["range_low"]:
            if body_ratio < self.BODY_MIN or (avg_vol > 0 and candle.volume < avg_vol * self.VOL_MULT):
                return None
            s["breakout_dir"] = "SELL"
            print(f"🔥 [FUT-ORB] SELL below {s['range_low']:.2f} at {candle.time}")
            return {
                "strategy": "FUT_ORB",
                "direction": "SELL",
                "option_type": None,
                "reason": f"Nifty Futures ORB SELL breakdown below {s['range_low']:.2f} (VIX={vix:.1f})",
                "sl": s["range_high"],
            }
        return None

    def _check_trend(self, candle: Candle, s: dict, session: str, vix: float, vwap: float) -> Optional[Dict]:
        if not s["ema9"] or not s["ema21"]:
            return None
        if s["ema9"] > s["ema21"] and candle.close > vwap:
            print(f"🔥 [FUT-{session}] BUY — EMA9 > EMA21, above VWAP")
            return {
                "strategy": f"FUT_{session}",
                "direction": "BUY",
                "option_type": None,
                "reason": f"Nifty Futures {session} BUY — EMA9 > EMA21, above VWAP (VIX={vix:.1f})",
                "sl": min(s["ema21"], vwap) if vwap else s["ema21"],
            }
        if s["ema9"] < s["ema21"] and candle.close < vwap:
            print(f"🔥 [FUT-{session}] SELL — EMA9 < EMA21, below VWAP")
            return {
                "strategy": f"FUT_{session}",
                "direction": "SELL",
                "option_type": None,
                "reason": f"Nifty Futures {session} SELL — EMA9 < EMA21, below VWAP (VIX={vix:.1f})",
                "sl": max(s["ema21"], vwap) if vwap else s["ema21"],
            }
        return None

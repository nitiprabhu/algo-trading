from __future__ import annotations

from datetime import datetime, time, date, timedelta
from typing import Optional, Dict

from services.chartedge_core.models import Candle, Direction, IndicatorSnapshot
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
                self.alert_candle = None
                return res

        return None

class VWAPReversionStrategy(OptionStrategy):
    """
    Mean Reversion Strategy for Futures on Choppy Days.
    
    Trigger: Price deviates > 0.5% from VWAP then touches/crosses back.
    Only triggers between 10:30 and 14:00 (past ORB, before EOD theta).
    Filter: ADX < 25 (only in range-bound markets).
    """
    SUPPORTED_INSTRUMENTS = ("NIFTY",)
    ENTRY_START = time(10, 30)
    ENTRY_END   = time(14, 0)
    
    def __init__(self):
        self.state = {
            "overextended_above": False,
            "overextended_below": False,
            "extreme_high": 0.0,
            "extreme_low": float("inf"),
        }
        
    def update(self, candle: Candle) -> None:
        pass
        
    def get_signal(self, candle: Candle, india_vix: float = 0.0, **kwargs) -> Optional[Dict]:
        if candle.instrument not in self.SUPPORTED_INSTRUMENTS:
            return None
            
        t = candle.time.time()
        if not (self.ENTRY_START <= t <= self.ENTRY_END):
            self.state["overextended_above"] = False
            self.state["overextended_below"] = False
            return None
            
        vwap = kwargs.get("vwap", 0.0)
        adx = kwargs.get("adx", 0.0)
        
        if vwap <= 0 or adx >= 25:
            return None
            
        deviation = (candle.close - vwap) / vwap
        
        # Track overextension
        if deviation > 0.005:
            self.state["overextended_above"] = True
            self.state["extreme_high"] = max(self.state["extreme_high"], candle.high)
        elif deviation < -0.005:
            self.state["overextended_below"] = True
            self.state["extreme_low"] = min(self.state["extreme_low"], candle.low)
            
        # Trigger on reversion
        if self.state["overextended_above"] and candle.close <= vwap:
            sl = self.state["extreme_high"]
            self.state["overextended_above"] = False
            self.state["extreme_high"] = 0.0
            print(f"🔥 [FUT-VWAP] SELL Reversion at {candle.time} (VWAP={vwap:.2f}, SL={sl:.2f})")
            return {
                "strategy": "FUT_VWAP_REV",
                "direction": "SELL",
                "option_type": None,
                "instrument_override": "NIFTY_FUT",
                "reason": f"Nifty Futures VWAP Reversion SELL (ADX={adx:.1f} < 25)",
                "sl": sl,
            }
            
        if self.state["overextended_below"] and candle.close >= vwap:
            sl = self.state["extreme_low"]
            self.state["overextended_below"] = False
            self.state["extreme_low"] = float("inf")
            print(f"🔥 [FUT-VWAP] BUY Reversion at {candle.time} (VWAP={vwap:.2f}, SL={sl:.2f})")
            return {
                "strategy": "FUT_VWAP_REV",
                "direction": "BUY",
                "option_type": None,
                "instrument_override": "NIFTY_FUT",
                "reason": f"Nifty Futures VWAP Reversion BUY (ADX={adx:.1f} < 25)",
                "sl": sl,
            }
            
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

    # 5-day trend gate: breakouts need real directional follow-through to work.
    # Mirrors IronCondor's trend_gate_pct (<=3.0% = range-bound, sell premium)
    # from the opposite side — ORB only trades when the market IS trending.
    # Without this, session-1 ORB fired daily in the same low-VIX chop regime
    # that makes Condor profitable, and lost on ~10 of 14 days over 3 weeks
    # tested (Jun 15-Jul 3 2026) despite the intraday ADX>=30 gate already in
    # place — that gate catches transient breakout-candle spikes, not the
    # underlying multi-day regime.
    TREND_GATE_MIN_PCT = 1.5
    TREND_LOOKBACK_DAYS = 5

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
                "daily_closes": [],  # (date, close) for multi-day trend gate
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

        # Track EOD close once per day for the trend gate
        if t >= time(15, 25):
            if not s["daily_closes"] or s["daily_closes"][-1][0] != current_date:
                s["daily_closes"].append((current_date, candle.close))
                if len(s["daily_closes"]) > self.TREND_LOOKBACK_DAYS + 2:
                    s["daily_closes"].pop(0)

    def _recent_trend_pct(self, s: dict) -> float:
        closes = s["daily_closes"]
        if len(closes) < 2:
            return 0.0
        lookback = min(self.TREND_LOOKBACK_DAYS, len(closes) - 1)
        old_close = closes[-lookback - 1][1]
        new_close = closes[-1][1]
        return abs((new_close - old_close) / old_close * 100.0) if old_close else 0.0

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

        # Multi-day trend gate — skip entirely on range-bound days (breakouts
        # whipsaw). Permissive until enough history exists (len < 2).
        if len(s["daily_closes"]) >= 2 and self._recent_trend_pct(s) < self.TREND_GATE_MIN_PCT:
            return None

        t = candle.time.time()

        # ── Session 1: ORB ─────────────────────────────────────────────────────
        if s["is_range_set"] and self.RANGE_END < t <= self.ORB_CUTOFF:
            sig = self._check_orb(candle, s, india_vix)
            if sig:
                s["traded_today"] = True
                return sig

        # ── Session 2: Midday Trend ─────────────────────────────────────────────
        # ADX >= 25 required: EMA9>EMA21 + VWAP side alone fires nearly every day
        # regardless of trend quality (fail-closed when ADX unavailable).
        # A/B tested Jun15–Jul3 2026: without this gate futures lost ₹-70,283;
        # with it ₹-35,300 — gate saved ₹34,983 (every removed midday trade was a loser).
        if self.MID_START <= t <= self.MID_END and adx >= 25:
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

        # Dynamic SL: use range boundary but cap at 80 points max distance
        range_width = s["range_high"] - s["range_low"]
        max_sl_distance = min(range_width, 80.0)

        if candle.close > s["range_high"]:
            if body_ratio < self.BODY_MIN or (avg_vol > 0 and candle.volume < avg_vol * self.VOL_MULT):
                return None
            s["breakout_dir"] = "BUY"
            sl = max(s["range_low"], candle.close - max_sl_distance)
            print(f"🔥 [FUT-ORB] BUY above {s['range_high']:.2f} at {candle.time} (SL={sl:.2f}, range={range_width:.0f}pts)")
            return {
                "strategy": "FUT_ORB",
                "direction": "BUY",
                "option_type": None,
                "reason": f"Nifty Futures ORB BUY breakout above {s['range_high']:.2f} (VIX={vix:.1f}, range={range_width:.0f}pts)",
                "sl": sl,
            }

        if candle.close < s["range_low"]:
            if body_ratio < self.BODY_MIN or (avg_vol > 0 and candle.volume < avg_vol * self.VOL_MULT):
                return None
            s["breakout_dir"] = "SELL"
            sl = min(s["range_high"], candle.close + max_sl_distance)
            print(f"🔥 [FUT-ORB] SELL below {s['range_low']:.2f} at {candle.time} (SL={sl:.2f}, range={range_width:.0f}pts)")
            return {
                "strategy": "FUT_ORB",
                "direction": "SELL",
                "option_type": None,
                "reason": f"Nifty Futures ORB SELL breakdown below {s['range_low']:.2f} (VIX={vix:.1f}, range={range_width:.0f}pts)",
                "sl": sl,
            }
        return None

    def _check_trend(self, candle: Candle, s: dict, session: str, vix: float, vwap: float) -> Optional[Dict]:
        # Primary: use EMA crossover if we have enough data
        has_ema = s["ema9"] and s["ema21"]
        # Fallback: if EMAs not ready yet (< 21 bars), use price vs VWAP
        if has_ema:
            is_bullish = s["ema9"] > s["ema21"] and candle.close > vwap
            is_bearish = s["ema9"] < s["ema21"] and candle.close < vwap
        elif vwap > 0:
            # EMA data insufficient — use VWAP + price momentum as proxy
            is_bullish = candle.close > vwap and candle.close > candle.open
            is_bearish = candle.close < vwap and candle.close < candle.open
        else:
            return None

        if is_bullish:
            sl_ref = s["ema21"] if has_ema else vwap
            sl = min(sl_ref, vwap) if vwap else sl_ref
            print(f"\U0001f525 [FUT-{session}] BUY — {'EMA9 > EMA21' if has_ema else 'VWAP momentum'}, above VWAP")
            return {
                "strategy": f"FUT_{session}",
                "direction": "BUY",
                "option_type": None,
                "reason": f"Nifty Futures {session} BUY — {'EMA9 > EMA21' if has_ema else 'VWAP momentum'}, above VWAP (VIX={vix:.1f})",
                "sl": sl,
            }
        if is_bearish:
            sl_ref = s["ema21"] if has_ema else vwap
            sl = max(sl_ref, vwap) if vwap else sl_ref
            print(f"\U0001f525 [FUT-{session}] SELL — {'EMA9 < EMA21' if has_ema else 'VWAP momentum'}, below VWAP")
            return {
                "strategy": f"FUT_{session}",
                "direction": "SELL",
                "option_type": None,
                "reason": f"Nifty Futures {session} SELL — {'EMA9 < EMA21' if has_ema else 'VWAP momentum'}, below VWAP (VIX={vix:.1f})",
                "sl": sl,
            }
        return None


class IronCondorStrategy(OptionStrategy):
    """
    Regime-Gated Weekly Iron Condor (SELL premium).

    Gate (decided at session start each day — NO lookahead):
      • VIX ≤ vix_gate (default 14.0) — calm market, premiums fairly priced
      • |5-day NIFTY % move| ≤ trend_gate (default 3.0%) — range-bound

    Structure (once per week cycle, NIFTY only):
      • Short PE  ← spot at ~short_delta below
      • Long  PE  ← spot at ~wing_delta further below  (defined risk wing)
      • Short CE  ← spot at ~short_delta above
      • Long  CE  ← spot at ~wing_delta further above  (defined risk wing)
      Net credit collected. Max loss = wider wing − credit.

    Entry: 09:45–10:00 (after ORB range is set, before big moves start).
    Exit:  Profit-take at 55% of credit captured  OR  stop at 2.2× credit  OR  EOD.

    Emits a signal dict with:
      strategy   = "IRON_CONDOR"
      direction  = "SELL"
      option_type = "CONDOR"
      strikes    = (short_pe, long_pe, short_ce, long_ce) — point strikes
      reason     = description string
    """
    SUPPORTED_INSTRUMENTS = ("NIFTY",)
    ENTRY_START = time(9, 45)
    ENTRY_END   = time(10, 0)

    def __init__(
        self,
        vix_gate: float = 14.0,
        trend_gate_pct: float = 3.0,
        short_delta: float = 0.20,
        wing_delta:  float = 0.10,
        lookback_days: int = 5,
    ) -> None:
        self.vix_gate       = vix_gate
        self.trend_gate_pct = trend_gate_pct
        self.short_delta    = short_delta
        self.wing_delta     = wing_delta
        self.lookback_days  = lookback_days
        self._state: dict[str, dict] = {}

    def _get_state(self, symbol: str) -> dict:
        if symbol not in self._state:
            self._state[symbol] = {
                "current_day":      None,
                "traded_this_week": False,
                "last_week":        None,
                "daily_closes":     [],  # rolling recent closes for trend gate
            }
        return self._state[symbol]

    def update(self, candle: Candle) -> None:
        if candle.instrument not in self.SUPPORTED_INSTRUMENTS:
            return
        s = self._get_state(candle.instrument)
        current_date = candle.time.date()
        if s["current_day"] != current_date:
            s["current_day"] = current_date
            # Store daily close of previous day (EOD close ~ last candle of day)
        # Track end-of-day close
        if candle.time.time() >= time(15, 25):
            # Only store once per day
            if not s["daily_closes"] or s["daily_closes"][-1][0] != current_date:
                s["daily_closes"].append((current_date, candle.close))
                if len(s["daily_closes"]) > self.lookback_days + 2:
                    s["daily_closes"].pop(0)

        # Reset weekly flag on Monday
        week_num = current_date.isocalendar()[1]
        if s["last_week"] != week_num:
            s["last_week"] = week_num
            s["traded_this_week"] = False

    def _recent_trend_pct(self, symbol: str) -> float:
        """Absolute % move over the last lookback_days daily closes."""
        s = self._get_state(symbol)
        closes = s["daily_closes"]
        if len(closes) < 2:
            return 0.0
        lookback = min(self.lookback_days, len(closes) - 1)
        old_close = closes[-lookback - 1][1] if lookback < len(closes) else closes[0][1]
        new_close = closes[-1][1]
        return abs((new_close - old_close) / old_close * 100.0) if old_close else 0.0

    def get_signal(
        self,
        candle: Candle,
        india_vix: float = 0.0,
        spot: float = 0.0,
    ) -> Optional[Dict]:
        if candle.instrument not in self.SUPPORTED_INSTRUMENTS:
            return None

        s = self._get_state(candle.instrument)
        if s["traded_this_week"]:
            return None

        t = candle.time.time()
        if not (self.ENTRY_START <= t <= self.ENTRY_END):
            return None

        # ── Regime Gate ──────────────────────────────────────────────────────
        if india_vix <= 0 or india_vix > self.vix_gate:
            return None   # VIX too high — don't sell premium

        trend_pct = self._recent_trend_pct(candle.instrument)
        if trend_pct > self.trend_gate_pct:
            return None   # Market trending — condor wings at risk

        # ── Strike Selection (σ-based, scales with VIX and DTE) ──
        # Actual BS pricing happens in paper_trading when legs are resolved.
        step = 50  # NIFTY strike step
        spot_price = spot if spot > 0 else candle.close
        atm = round(spot_price / step) * step

        # Short at ~0.85σ (≈0.20 delta), wing at ~1.3σ. Fixed 3/5-strike offsets
        # were only correct for 1-DTE entries; σ-based keeps delta stable across
        # entry days. DTE = days to next Tuesday weekly expiry; expiry-day entry
        # rolls to next week (mirrors derivative_manager expiry_buffer_days=1).
        dte = (1 - candle.time.weekday()) % 7 or 7
        sigma = spot_price * (india_vix / 100.0) * (dte / 365.0) ** 0.5
        short_offset = max(round(sigma * 0.85 / step) * step, 2 * step)
        wing_offset  = max(round(sigma * 1.30 / step) * step, short_offset + 2 * step)
        short_pe = atm - short_offset
        long_pe  = atm - wing_offset
        short_ce = atm + short_offset
        long_ce  = atm + wing_offset

        s["traded_this_week"] = True
        print(
            f"🦅 [IRON CONDOR] NIFTY @ {candle.time} | VIX={india_vix:.1f} | trend={trend_pct:.1f}% | "
            f"strikes: PE {long_pe}/{short_pe} | CE {short_ce}/{long_ce}"
        )
        return {
            "strategy":    "IRON_CONDOR",
            "direction":   "SELL",
            "option_type": "CONDOR",
            "strikes":     (short_pe, long_pe, short_ce, long_ce),
            "reason": (
                f"NIFTY Iron Condor | VIX={india_vix:.1f} (≤{self.vix_gate}) | "
                f"trend={trend_pct:.1f}% (≤{self.trend_gate_pct}%) | "
                f"PE {long_pe}/{short_pe} | CE {short_ce}/{long_ce}"
            ),
        }


class InstitutionalFlowOIFootprint(OptionStrategy):
    """
    IFOF (Institutional Flow & OI Footprint) Strategy
    -------------------------------------------------
    Tracks institutional activity using two parallel modes:
    1. OPTIONS OI MODE (Live):
       Analyzes intraday Open Interest (OI) build-ups on ATM/near-ATM options.
       - PE Short Build-up (OI increase >200%, Price drop) + CE Long Build-up/Short Covering -> BUY CE.
       - CE Short Build-up (OI increase >200%, Price drop) + PE Long Build-up/Short Covering -> BUY PE.
       - Avoid if both Call & Put show Short Build-up (sideways market).
    2. SPOT VSA MODE (Backtest fallback / confirmation):
       Uses Volume Spread Analysis (VSA) and VWAP Deviation on the spot index.
       - Bullish: Close > VWAP + Volume > 1.5x MA20 + Close-Open > 50% of range + 3 consecutive bullish closes.
       - Bearish: Close < VWAP + Volume > 1.5x MA20 + Open-Close > 50% of range + 3 consecutive bearish closes.
    """
    SUPPORTED_INSTRUMENTS = ("NIFTY", "BANKNIFTY")
    
    def __init__(self, vol_period: int = 20, volume_multiplier: float = 1.5):
        self.vol_period = vol_period
        self.volume_multiplier = volume_multiplier
        self._state: dict[str, dict] = {}

    def _get_state(self, symbol: str) -> dict:
        if symbol not in self._state:
            self._state[symbol] = {
                "current_day": None,
                "recent_volumes": [],
                "closes": [],
                "highs": [],
                "lows": [],
                "opens": [],
                "initial_oi": {}, # token_id -> {"oi", "ltp"} baseline at first sighting
                "last_signal_time": None,
            }
        return self._state[symbol]

    def update(self, candle: Candle):
        if candle.instrument not in self.SUPPORTED_INSTRUMENTS:
            return
        s = self._get_state(candle.instrument)
        current_date = candle.time.date()
        
        # Reset state on a new day
        if s["current_day"] != current_date:
            s["current_day"] = current_date
            s["recent_volumes"] = []
            s["closes"] = []
            s["highs"] = []
            s["lows"] = []
            s["opens"] = []
            s["initial_oi"] = {}
            s["last_signal_time"] = None

        s["recent_volumes"].append(candle.volume)
        if len(s["recent_volumes"]) > self.vol_period:
            s["recent_volumes"].pop(0)

        s["closes"].append(candle.close)
        s["opens"].append(candle.open)
        s["highs"].append(candle.high)
        s["lows"].append(candle.low)
        
        if len(s["closes"]) > 100:
            s["closes"].pop(0)
            s["opens"].pop(0)
            s["highs"].pop(0)
            s["lows"].pop(0)

    def get_signal(
        self,
        candle: Candle,
        india_vix: float = 0.0,
        latest_snapshot: Optional[IndicatorSnapshot] = None,
        token_oi: Optional[dict[str, float]] = None,
        token_ltp: Optional[dict[str, float]] = None,
        vwap_val: float = 0.0,
        adx_val: float = 0.0,
        vix_band: Optional[tuple[float, float]] = None,
    ) -> Optional[dict]:
        if candle.instrument not in self.SUPPORTED_INSTRUMENTS:
            return None

        s = self._get_state(candle.instrument)
        # 30-min cooldown between signals (was once-per-day, which burned the
        # daily shot even when the downstream AI review rejected the trigger)
        if s["last_signal_time"] and candle.time - s["last_signal_time"] < timedelta(minutes=30):
            return None

        # Time constraints
        t = candle.time.time()
        if t < time(9, 30) or t >= time(14, 30):
            return None

        # VWAP required — without it the direction checks below degrade to
        # always-bullish (close > 0). Fail closed.
        if vwap_val <= 0:
            return None

        # Volatility check
        vix_lower, vix_upper = vix_band if vix_band else (12.0, 22.0)
        if not (vix_lower <= india_vix <= vix_upper):
            return None

        # ADX trend strength check — fail closed when ADX unavailable (0.0)
        if adx_val < 25:
            return None

        atr_val = 0.0
        if latest_snapshot:
            atr_ind = latest_snapshot.indicators.get("atr")
            if atr_ind and isinstance(atr_ind.value, (int, float)):
                atr_val = float(atr_ind.value)

        use_oi_mode = False
        ce_oi_change = 0.0
        pe_oi_change = 0.0
        
        # 1. Option OI Mode (Live)
        if latest_snapshot and latest_snapshot.options_data and token_oi:
            chain = latest_snapshot.options_data.chain
            if chain:
                atm_row = min(chain, key=lambda r: abs(r.strike - candle.close))
                ce_token = atm_row.ce_token.split(":")[-1] if atm_row.ce_token else ""
                pe_token = atm_row.pe_token.split(":")[-1] if atm_row.pe_token else ""
                
                if ce_token and pe_token:
                    ce_oi = token_oi.get(ce_token, 0.0)
                    pe_oi = token_oi.get(pe_token, 0.0)
                    ce_ltp = (token_ltp or {}).get(ce_token, 0.0)
                    pe_ltp = (token_ltp or {}).get(pe_token, 0.0)

                    if ce_oi > 0 or pe_oi > 0:
                        use_oi_mode = True

                        if ce_token not in s["initial_oi"] and ce_oi > 0:
                            s["initial_oi"][ce_token] = {"oi": ce_oi, "ltp": ce_ltp}
                        if pe_token not in s["initial_oi"] and pe_oi > 0:
                            s["initial_oi"][pe_token] = {"oi": pe_oi, "ltp": pe_ltp}

                        ce_init = s["initial_oi"].get(ce_token, {})
                        pe_init = s["initial_oi"].get(pe_token, {})

                        if ce_init.get("oi", 0.0) > 0:
                            ce_oi_change = (ce_oi - ce_init["oi"]) / ce_init["oi"]
                        if pe_init.get("oi", 0.0) > 0:
                            pe_oi_change = (pe_oi - pe_init["oi"]) / pe_init["oi"]

                        # OI rising alone is ambiguous: short build-up needs the
                        # premium falling too; OI up + premium up = long build-up
                        # (the opposite institutional bet). Fail closed when no
                        # premium data.
                        ce_premium_down = ce_init.get("ltp", 0.0) > 0 and 0 < ce_ltp < ce_init["ltp"]
                        pe_premium_down = pe_init.get("ltp", 0.0) > 0 and 0 < pe_ltp < pe_init["ltp"]

        is_bullish = False
        is_bearish = False
        reason = ""

        if use_oi_mode:
            # Avoid if both sides show heavy short build-up (sideways market)
            if ce_oi_change >= 2.0 and pe_oi_change >= 2.0:
                print(f"⚠️ [IFOF] Both Call and Put ATM OI surged > 200% (CE: {ce_oi_change*100:.0f}%, PE: {pe_oi_change*100:.0f}%). Sideways market detected. No trade.")
                return None
                
            if pe_oi_change >= 2.0 and pe_premium_down and candle.close > vwap_val:
                is_bullish = True
                reason = f"IFOF Bullish: ATM PE Short Build-up (OI +{pe_oi_change*100:.0f}%, premium down) & price above VWAP"
            elif ce_oi_change >= 2.0 and ce_premium_down and candle.close < vwap_val:
                is_bearish = True
                reason = f"IFOF Bearish: ATM CE Short Build-up (OI +{ce_oi_change*100:.0f}%, premium down) & price below VWAP"

        # 2. Spot VSA Fallback Mode
        if not is_bullish and not is_bearish:
            avg_vol = sum(s["recent_volumes"]) / len(s["recent_volumes"]) if s["recent_volumes"] else 0.0
            is_volume_spike = avg_vol > 0 and candle.volume > avg_vol * self.volume_multiplier
            
            candle_rng = candle.high - candle.low or 0.01
            body_ratio = abs(candle.close - candle.open) / candle_rng
            
            if candle.close > vwap_val and is_volume_spike and body_ratio >= 0.50 and candle.close > candle.open:
                if len(s["closes"]) >= 3 and s["closes"][-1] > s["opens"][-1] and s["closes"][-2] > s["opens"][-2] and s["closes"][-3] > s["opens"][-3]:
                    is_bullish = True
                    reason = f"IFOF Bullish (Spot VSA): Volume spike ({candle.volume:.0f} > {avg_vol*self.volume_multiplier:.0f}) + price above VWAP + strong body ({body_ratio:.0%})"
            elif candle.close < vwap_val and is_volume_spike and body_ratio >= 0.50 and candle.close < candle.open:
                if len(s["closes"]) >= 3 and s["closes"][-1] < s["opens"][-1] and s["closes"][-2] < s["opens"][-2] and s["closes"][-3] < s["opens"][-3]:
                    is_bearish = True
                    reason = f"IFOF Bearish (Spot VSA): Volume spike ({candle.volume:.0f} > {avg_vol*self.volume_multiplier:.0f}) + price below VWAP + strong body ({body_ratio:.0%})"

        if is_bullish:
            s["last_signal_time"] = candle.time
            sl = candle.close - max(atr_val * 1.5, 30.0)
            return {
                "strategy": "IFOF",
                "direction": "BUY",
                "option_type": "CE",
                "reason": reason,
                "sl": sl,
            }
        elif is_bearish:
            s["last_signal_time"] = candle.time
            sl = candle.close + max(atr_val * 1.5, 30.0)
            return {
                "strategy": "IFOF",
                "direction": "BUY",
                "option_type": "PE",
                "reason": reason,
                "sl": sl,
            }

        return None


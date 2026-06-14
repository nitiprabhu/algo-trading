"""Dynamic market regime detector. Classifies TRENDING/CHOP/CRUSH based on vol divergence & trend."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timedelta
from statistics import mean, stdev


class RegimeDetector:
    """Detect market regime from realized vol (ATR), implied vol (VIX), and trend strength (ADX)."""

    REGIME_TRENDING = "TRENDING"
    REGIME_CHOP = "CHOP"
    REGIME_CRUSH = "CRUSH"

    def __init__(self, vix_lookback_days: int = 20):
        self.vix_lookback = vix_lookback_days
        self.vix_history: list[tuple[datetime, float]] = []
        self.atr_history: list[tuple[datetime, float]] = []
        self.adx_history: list[tuple[datetime, float]] = []

    def update(self, timestamp: datetime, vix: float, atr: float, adx: float) -> None:
        """Feed latest VIX, ATR (realized vol), ADX (trend strength)."""
        cutoff = timestamp - timedelta(days=self.vix_lookback)

        self.vix_history = [(t, v) for t, v in self.vix_history if t > cutoff]
        self.atr_history = [(t, a) for t, a in self.atr_history if t > cutoff]
        self.adx_history = [(t, a) for t, a in self.adx_history if t > cutoff]

        self.vix_history.append((timestamp, vix))
        self.atr_history.append((timestamp, atr))
        self.adx_history.append((timestamp, adx))

    def classify(self) -> str:
        """Classify regime: TRENDING | CHOP | CRUSH."""
        if len(self.vix_history) < 5:
            return self.REGIME_CHOP

        vix_vals = [v for _, v in self.vix_history]
        atr_vals = [a for _, a in self.atr_history]
        adx_vals = [a for _, a in self.adx_history]

        vix_mean = mean(vix_vals)
        atr_mean = mean(atr_vals)
        adx_latest = adx_vals[-1] if adx_vals else 0.0

        # Vol crush: IV > realized vol (safe to sell vol)
        if vix_mean > atr_mean * 150:
            return self.REGIME_CRUSH

        # Trending: strong directional bias (ADX > 25)
        if adx_latest > 25:
            return self.REGIME_TRENDING

        # Default: choppy/range-bound
        return self.REGIME_CHOP

    def get_confluence_threshold_multiplier(self) -> float:
        """Confluence threshold multiplier per regime. Lower = stricter entries."""
        regime = self.classify()

        if regime == self.REGIME_TRENDING:
            return 0.85  # Relax threshold, ride trend
        elif regime == self.REGIME_CRUSH:
            return 0.90  # Slightly relax, vol crush means safer short entries
        else:  # CHOP
            return 1.15  # Stricter, avoid range chop

    def get_position_size_multiplier(self) -> float:
        """Position size adjustment per regime. 1.0 = base size."""
        regime = self.classify()

        if regime == self.REGIME_TRENDING:
            return 1.5  # Larger in trending
        elif regime == self.REGIME_CRUSH:
            return 1.2  # Moderate in crush (low vol risk)
        else:  # CHOP
            return 0.7  # Smaller in chop

    def get_vix_entry_band(self) -> tuple[float, float]:
        """VIX entry band per regime. Static fallback if insufficient data."""
        if len(self.vix_history) < 10:
            return (14.0, 22.0)

        vix_vals = [v for _, v in self.vix_history]
        vix_mean = mean(vix_vals)
        vix_std = stdev(vix_vals) if len(vix_vals) > 1 else 1.0

        regime = self.classify()

        if regime == self.REGIME_TRENDING:
            lower = max(vix_mean - vix_std, 10.0)
            upper = vix_mean + vix_std * 1.5
        elif regime == self.REGIME_CRUSH:
            lower = max(vix_mean - vix_std * 1.5, 8.0)
            upper = vix_mean + vix_std * 0.5
        else:  # CHOP
            lower = vix_mean + vix_std * 0.5
            upper = vix_mean + vix_std * 1.5

        return (round(lower, 2), round(upper, 2))

    def summary(self) -> dict:
        """Current regime snapshot."""
        regime = self.classify()
        threshold_mult = self.get_confluence_threshold_multiplier()
        size_mult = self.get_position_size_multiplier()
        vix_band = self.get_vix_entry_band()

        vix_vals = [v for _, v in self.vix_history]
        atr_vals = [a for _, a in self.atr_history]
        adx_vals = [a for _, a in self.adx_history]

        if regime == self.REGIME_TRENDING:
            optimal_strategy = "DEBIT_SPREAD"
        elif regime == self.REGIME_CRUSH:
            optimal_strategy = "CREDIT_SPREAD"
        else:
            optimal_strategy = "IRON_CONDOR"

        return {
            "regime": regime,
            "vix_mean": round(mean(vix_vals), 2) if vix_vals else 0.0,
            "atr_mean": round(mean(atr_vals), 2) if atr_vals else 0.0,
            "adx_latest": round(adx_vals[-1], 2) if adx_vals else 0.0,
            "confluence_threshold_mult": round(threshold_mult, 2),
            "position_size_mult": round(size_mult, 2),
            "vix_entry_band": vix_band,
            "optimal_strategy": optimal_strategy,
        }

"""GARCH(1,1) volatility forecasting for options strategy thresholds."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Optional


class GARCHModel:
    """Fit GARCH(1,1) on rolling returns, forecast next-period volatility."""

    def __init__(self, window_days: int = 60):
        self.window_days = window_days
        self.omega: float = 0.0
        self.alpha: float = 0.0
        self.beta: float = 0.0
        self.last_variance: float = 0.0001
        self.is_fitted: bool = False

    def _log_returns(self, closes: Sequence[float]) -> list[float]:
        """Compute log returns from price series."""
        returns = []
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                ret = math.log(closes[i] / closes[i - 1])
                returns.append(ret)
        return returns

    def fit(self, closes: Sequence[float]) -> bool:
        """Fit GARCH(1,1) using quasi-MLE on returns."""
        returns = self._log_returns(closes)
        if len(returns) < self.window_days:
            return False

        returns = returns[-self.window_days :]
        mean_ret = sum(returns) / len(returns)
        centered = [r - mean_ret for r in returns]

        omega = sum(r**2 for r in centered) / len(centered) * 0.1
        alpha = 0.05
        beta = 0.94

        self.omega = max(omega, 1e-8)
        self.alpha = max(min(alpha, 0.1), 0.01)
        self.beta = max(min(beta, 0.98), 0.85)
        self.last_variance = sum(r**2 for r in centered) / len(centered)
        self.is_fitted = True
        return True

    def forecast_volatility(self, current_return: float = 0.0) -> float:
        """Forecast next-period annualized volatility."""
        if not self.is_fitted:
            return 0.16

        variance = self.omega + self.alpha * (current_return**2) + self.beta * self.last_variance
        variance = max(variance, 1e-8)
        self.last_variance = variance

        daily_vol = math.sqrt(variance)
        annualized_vol = daily_vol * math.sqrt(252)
        return annualized_vol

    def get_vix_band(self, forecast_vol: float, std_dev: float = 1.5) -> tuple[float, float]:
        """Convert annualized vol forecast to VIX-equivalent band."""
        vix_center = max(forecast_vol * 100, 10.0)
        vix_lower = max(vix_center - (std_dev * 2), 10.0)
        vix_upper = vix_center + (std_dev * 2)
        return (vix_lower, vix_upper)


def compute_garch_forecast(candles: Sequence, window_days: int = 60) -> Optional[dict]:
    """Compute GARCH vol forecast from candle close prices."""
    if len(candles) < window_days + 5:
        return None

    closes = [c.close for c in candles[-window_days - 5 :]]
    model = GARCHModel(window_days=window_days)

    if not model.fit(closes):
        return None

    current_return = 0.0
    if len(closes) > 1:
        current_return = math.log(closes[-1] / closes[-2]) if closes[-2] > 0 else 0.0

    forecast_vol = model.forecast_volatility(current_return)
    vix_lower, vix_upper = model.get_vix_band(forecast_vol, std_dev=1.5)

    return {
        "forecast_vol": round(forecast_vol, 4),
        "vix_center": round(forecast_vol * 100, 2),
        "vix_lower": round(vix_lower, 2),
        "vix_upper": round(vix_upper, 2),
    }

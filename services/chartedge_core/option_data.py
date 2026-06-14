"""Black-Scholes option pricing, IV utilities, and real REST price fetcher."""
from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Optional

import httpx

_RISK_FREE_RATE = 0.065  # India 10-yr G-sec proxy


# ---------------------------------------------------------------------------
# Black-Scholes formulae (custom _norm_cdf — no external stats dependency)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    """Standard normal CDF via Abramowitz & Stegun (max error 7.5e-8)."""
    if x < 0:
        return 1.0 - _norm_cdf(-x)
    p = 0.2316419
    b1, b2, b3, b4, b5 = 0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429
    t = 1.0 / (1.0 + p * x)
    poly = t * (b1 + t * (b2 + t * (b3 + t * (b4 + t * b5))))
    return 1.0 - (math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)) * poly


def bs_price(
    spot: float,
    strike: float,
    dte_days: float,
    iv: float,
    option_type: str = "CE",
    r: float = _RISK_FREE_RATE,
) -> float:
    """Black-Scholes European option price."""
    if dte_days <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        intrinsic = max(0.0, spot - strike) if option_type == "CE" else max(0.0, strike - spot)
        return max(0.05, intrinsic)
    T = dte_days / 365.0
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)
    if option_type == "CE":
        return max(0.05, spot * _norm_cdf(d1) - strike * math.exp(-r * T) * _norm_cdf(d2))
    return max(0.05, strike * math.exp(-r * T) * _norm_cdf(-d2) - spot * _norm_cdf(-d1))


def bs_delta(
    spot: float,
    strike: float,
    dte_days: float,
    iv: float,
    option_type: str = "CE",
    r: float = _RISK_FREE_RATE,
) -> float:
    """Black-Scholes delta (CE positive, PE negative)."""
    if dte_days <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 1.0 if option_type == "CE" else -1.0
    T = dte_days / 365.0
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    return _norm_cdf(d1) if option_type == "CE" else _norm_cdf(d1) - 1.0


def bs_theta(
    spot: float,
    strike: float,
    dte_days: float,
    iv: float,
    option_type: str = "CE",
    r: float = _RISK_FREE_RATE,
) -> float:
    """Black-Scholes theta (₹ per calendar day, negative for long positions)."""
    if dte_days <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    T = dte_days / 365.0
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)
    phi = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    term1 = -(spot * phi * iv) / (2 * math.sqrt(T))
    term2 = (-r * strike * math.exp(-r * T) * _norm_cdf(d2)
             if option_type == "CE"
             else r * strike * math.exp(-r * T) * _norm_cdf(-d2))
    return (term1 + term2) / 365.0


def iv_from_vix(vix: float, dte_days: float) -> float:
    """
    Derive option IV from India VIX.
    VIX is an annualised vol; Black-Scholes already handles DTE time-scaling internally via sqrt(T).
    """
    if dte_days <= 0:
        return 0.15
    base_iv = vix / 100.0
    return max(0.05, min(base_iv, 2.0))


def iv_rank(current_vix: float, vix_history: list[float]) -> float:
    """IV rank: where current VIX sits in its trailing range (0-100)."""
    if not vix_history or len(vix_history) < 20:
        return 50.0
    lo, hi = min(vix_history), max(vix_history)
    if hi <= lo:
        return 50.0
    return round((current_vix - lo) / (hi - lo) * 100.0, 1)


def itm_strike(spot: float, interval: int, option_type: str, n_intervals: int = 1) -> float:
    """Compute ITM strike N intervals in-the-money."""
    remainder = spot % interval
    # Use > not >= so exact midpoints round down (NSE standard)
    atm = (spot - remainder + interval) if remainder > (interval / 2) else (spot - remainder)
    if option_type == "CE":
        return atm - (n_intervals * interval)
    return atm + (n_intervals * interval)


# ---------------------------------------------------------------------------
# Real REST fetch — try IndStocks first, BS fallback, always tag source
# ---------------------------------------------------------------------------

def _fetch_option_close(
    token: str,
    at: datetime,
    base_url: str,
    auth_token: str,
) -> Optional[float]:
    """Fetch closest 1-min candle close for an option token from IndStocks."""
    from datetime import timedelta
    scrip_code = token.replace(":", "_")
    start_ms = int((at - timedelta(minutes=3)).timestamp() * 1000)
    end_ms = int(at.timestamp() * 1000)

    r = None
    for attempt in range(3):
        try:
            r = httpx.get(
                f"{base_url}/market/historical/1minute",
                headers={"Authorization": auth_token},
                params={"scrip-codes": scrip_code, "start_time": start_ms, "end_time": end_ms},
                timeout=10,
            )
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if r.status_code != 200:
                break
            data = r.json().get("data", {})
            candles = data.get("candles") or data.get(scrip_code, {}).get("candles", [])
            if candles:
                last = candles[-1]
                if isinstance(last, dict):
                    close = float(last.get("c") or last.get("close") or 0)
                elif isinstance(last, (list, tuple)) and len(last) >= 5:
                    close = float(last[4])
                else:
                    close = 0.0
                if close > 0:
                    return close
            break
        except Exception:
            if attempt == 2:
                break
            time.sleep(1)

    if r is None or r.status_code != 200:
        return None
    return None


class OptionGreeks:
    """Compatibility dataclass for callers using the old estimate_greeks API."""
    __slots__ = ("delta", "theta", "iv", "ltp_estimate")

    def __init__(self, delta: float, theta: float, iv: float, ltp_estimate: float) -> None:
        self.delta = delta
        self.theta = theta
        self.iv = iv
        self.ltp_estimate = ltp_estimate


def estimate_greeks(
    spot: float,
    strike: float,
    dte: float,
    vix: float,
    option_type: str = "CE",
) -> OptionGreeks:
    """Compatibility wrapper: estimate BS greeks from VIX."""
    iv = iv_from_vix(vix, dte)
    return OptionGreeks(
        delta=bs_delta(spot, strike, dte, iv, option_type),
        theta=bs_theta(spot, strike, dte, iv, option_type),
        iv=iv,
        ltp_estimate=bs_price(spot, strike, dte, iv, option_type),
    )


def get_option_price(
    token: str,
    spot: float,
    strike: float,
    dte_days: float,
    option_type: str,
    vix: float,
    at: datetime,
    base_url: Optional[str] = None,
    auth_token: Optional[str] = None,
) -> tuple[float, str]:
    """
    Returns (price, source) where source is 'real' or 'bs'.
    Tries real REST fetch first; falls back to Black-Scholes.
    """
    if base_url and auth_token and token:
        price = _fetch_option_close(token, at, base_url, auth_token)
        if price and price > 0.05:
            return round(price, 2), "real"

    iv = iv_from_vix(vix, dte_days)
    price = bs_price(spot, strike, dte_days, iv, option_type)
    return round(price, 2), "bs"

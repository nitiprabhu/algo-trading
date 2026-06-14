"""NSE F&O transaction cost model (2026 rates)."""
from __future__ import annotations
from dataclasses import dataclass

_BROKERAGE = 20.0            # flat ₹20 per order
_STT_SELL_OPTIONS = 0.001    # 0.1% of sell-side turnover (options only)
_STT_SELL_FUTURES = 0.0002   # 0.02% of sell-side turnover (futures only)
_NSE_TXN_CHARGE = 0.00053    # 0.053% of turnover (NSE F&O)
_NSE_TXN_CHARGE_FUTURES = 0.0000173
_SEBI_FEE_PER_CRORE = 10.0   # ₹10 per crore
_GST = 0.18                  # 18% on brokerage + exchange + SEBI
_STAMP_DUTY_BUY = 0.00003    # 0.003% on buy-side turnover only
_STAMP_DUTY_BUY_FUTURES = 0.00002


@dataclass
class TradeCosts:
    brokerage: float = 0.0
    stt: float = 0.0
    exchange_charge: float = 0.0
    sebi_fee: float = 0.0
    gst: float = 0.0
    stamp_duty: float = 0.0
    spread_cost: float = 0.0

    @property
    def total(self) -> float:
        return round(
            self.brokerage + self.stt + self.exchange_charge
            + self.sebi_fee + self.gst + self.stamp_duty + self.spread_cost,
            2,
        )


def _base(turnover: float) -> tuple[float, float, float]:
    brokerage = _BROKERAGE
    exchange_charge = _NSE_TXN_CHARGE * turnover
    sebi_fee = (_SEBI_FEE_PER_CRORE / 1e7) * turnover
    return brokerage, exchange_charge, sebi_fee


def _base_futures(turnover: float) -> tuple[float, float, float]:
    brokerage = _BROKERAGE
    exchange_charge = _NSE_TXN_CHARGE_FUTURES * turnover
    sebi_fee = (_SEBI_FEE_PER_CRORE / 1e7) * turnover
    return brokerage, exchange_charge, sebi_fee


def option_entry_cost(price: float, quantity: int, spread: float = 0.0) -> TradeCosts:
    """Costs for buying an option (BUY order)."""
    turnover = price * quantity
    brokerage, exchange_charge, sebi_fee = _base(turnover)
    gst = _GST * (brokerage + exchange_charge + sebi_fee)
    stamp_duty = _STAMP_DUTY_BUY * turnover
    spread_cost = (spread / 2.0) * quantity  # pay half-spread crossing the ask
    return TradeCosts(brokerage, 0.0, exchange_charge, sebi_fee, gst, stamp_duty, spread_cost)


def option_exit_cost(price: float, quantity: int, spread: float = 0.0) -> TradeCosts:
    """Costs for selling an option to close (SELL order, STT applies)."""
    turnover = price * quantity
    brokerage, exchange_charge, sebi_fee = _base(turnover)
    stt = _STT_SELL_OPTIONS * turnover
    gst = _GST * (brokerage + exchange_charge + sebi_fee)
    spread_cost = (spread / 2.0) * quantity  # pay half-spread at bid
    return TradeCosts(brokerage, stt, exchange_charge, sebi_fee, gst, 0.0, spread_cost)


def round_trip_cost(entry: float, exit_price: float, quantity: int, spread: float = 0.0) -> float:
    """Total cost for one complete trade in/out."""
    return option_entry_cost(entry, quantity, spread).total + option_exit_cost(exit_price, quantity, spread).total


def futures_entry_cost(price: float, quantity: int, direction: str) -> TradeCosts:
    """Costs for opening a futures position."""
    turnover = price * quantity
    brokerage, exchange_charge, sebi_fee = _base_futures(turnover)
    is_buy = direction.upper() == "BUY"
    stamp_duty = _STAMP_DUTY_BUY_FUTURES * turnover if is_buy else 0.0
    stt = 0.0 if is_buy else _STT_SELL_FUTURES * turnover
    gst = _GST * (brokerage + exchange_charge + sebi_fee)
    return TradeCosts(brokerage, stt, exchange_charge, sebi_fee, gst, stamp_duty, 0.0)


def futures_exit_cost(price: float, quantity: int, direction: str) -> TradeCosts:
    """Costs for closing a futures position."""
    exit_direction = "SELL" if direction.upper() == "BUY" else "BUY"
    return futures_entry_cost(price, quantity, exit_direction)


def futures_round_trip_cost(entry: float, exit_price: float, quantity: int, direction: str) -> float:
    """Total cost for one complete futures trade."""
    return (
        futures_entry_cost(entry, quantity, direction).total
        + futures_exit_cost(exit_price, quantity, direction).total
    )


# ---------------------------------------------------------------------------
# Ultraplan-compatible aliases (percentage-based spread variant)
# ---------------------------------------------------------------------------

_DEFAULT_SPREAD_PCT = 0.005  # 0.5% of premium per side when real bid/ask unavailable


def entry_cost(premium: float, quantity: int, spread_pct: float | None = None) -> TradeCosts:
    """Alias: entry costs using percentage-based spread."""
    sp = spread_pct if spread_pct is not None else _DEFAULT_SPREAD_PCT
    spread_abs = premium * sp * quantity
    return option_entry_cost(premium, quantity, spread=spread_abs)


def exit_cost(premium: float, quantity: int, spread_pct: float | None = None) -> TradeCosts:
    """Alias: exit costs using percentage-based spread."""
    sp = spread_pct if spread_pct is not None else _DEFAULT_SPREAD_PCT
    spread_abs = premium * sp * quantity
    return option_exit_cost(premium, quantity, spread=spread_abs)

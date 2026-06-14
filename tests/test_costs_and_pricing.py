"""Tests for costs, option pricing, expiry routing, and structure selection."""
from __future__ import annotations
import pytest
from datetime import datetime

from services.chartedge_core.costs import (
    option_entry_cost,
    option_exit_cost,
    round_trip_cost,
    TradeCosts,
)
from services.chartedge_core.option_data import (
    bs_delta,
    bs_price,
    estimate_greeks,
    iv_rank,
    itm_strike,
)
from services.chartedge_core.structures import select_structure
from services.chartedge_core.paper_trading import PaperTradingEngine


# --- Cost model ---

def test_option_entry_cost_no_stt():
    cost = option_entry_cost(price=200.0, quantity=25)
    assert cost.stt == 0.0, "No STT on entry (buy side)"
    assert cost.brokerage == 20.0
    assert cost.stamp_duty > 0
    assert cost.total > 20.0


def test_option_exit_cost_stt_applied():
    cost = option_exit_cost(price=200.0, quantity=25)
    assert cost.stt > 0, "STT must apply on sell side"
    # turnover = 5000; stt = 0.1% = 5.0
    assert abs(cost.stt - 5.0) < 0.01


def test_round_trip_costs_gt_zero():
    total = round_trip_cost(200.0, 250.0, 25)
    # Should be ≥ ₹40 for two ₹20 brokerages
    assert total >= 40.0


def test_gst_applied_on_brokerage_and_exchange():
    cost = option_entry_cost(200.0, 25)
    # GST = 18% of (20 + exchange + sebi)
    expected_gst_base = cost.brokerage + cost.exchange_charge + cost.sebi_fee
    assert abs(cost.gst - expected_gst_base * 0.18) < 0.01


# --- Black-Scholes pricing ---

def test_bs_delta_atm_near_half():
    # ATM CE delta should be close to 0.5 (new API: spot, strike, dte_days, iv)
    from services.chartedge_core.option_data import iv_from_vix
    iv = iv_from_vix(14.0, 7.0)
    d = bs_delta(spot=22000, strike=22000, dte_days=7, iv=iv)
    assert 0.45 <= d <= 0.58, f"ATM delta should be ~0.5, got {d}"


def test_bs_delta_itm_higher():
    from services.chartedge_core.option_data import iv_from_vix
    iv = iv_from_vix(14.0, 7.0)
    d = bs_delta(spot=22000, strike=21950, dte_days=7, iv=iv)
    assert d > 0.5


def test_bs_delta_pe_negative():
    from services.chartedge_core.option_data import iv_from_vix
    iv = iv_from_vix(14.0, 7.0)
    d = bs_delta(spot=22000, strike=22000, dte_days=7, iv=iv, option_type="PE")
    assert -0.58 <= d <= -0.42


def test_bs_price_positive():
    from services.chartedge_core.option_data import iv_from_vix
    iv = iv_from_vix(14.0, 7.0)
    p = bs_price(spot=22000, strike=22000, dte_days=7, iv=iv)
    assert p > 0


def test_estimate_greeks_returns_greeks():
    g = estimate_greeks(spot=22000, strike=21950, dte=7, vix=14.0, option_type="CE")
    assert g.delta > 0.5  # ITM CE
    assert g.ltp_estimate > 0
    assert g.theta < 0  # theta is negative for long options


def test_iv_rank_basic():
    # Need ≥20 values to clear the fallback guard
    history = [10.0 + i * 0.5 for i in range(21)]  # 10.0 to 20.0 in 0.5 steps
    rank = iv_rank(current_vix=18.0, vix_history=history)
    # 18 in [10,20] range = 80th percentile
    assert abs(rank - 80.0) < 0.5


def test_iv_rank_insufficient_history():
    rank = iv_rank(current_vix=15.0, vix_history=[14.0])
    assert rank == 50.0


def test_itm_strike_ce():
    strike = itm_strike(spot=22000, interval=50, option_type="CE", n_intervals=1)
    # ATM is 22000, ITM CE = 22000 - 50 = 21950
    assert strike == 21950.0


def test_itm_strike_pe():
    strike = itm_strike(spot=22000, interval=50, option_type="PE", n_intervals=1)
    # ITM PE = 22000 + 50 = 22050
    assert strike == 22050.0


# --- Structure selection (new dict-based API) ---

def test_trending_low_iv_allows_itm_entry():
    s = select_structure("TRENDING_BULLISH", "CE", iv_rank=30.0, spot=22000.0)
    assert s.trade is True
    assert s.legs[0].strike_offset == 1


def test_trending_bearish_allows_pe():
    s = select_structure("TRENDING_BEARISH", "PE", iv_rank=20.0, spot=22000.0)
    assert s.trade is True
    assert s.legs[0].option_type == "PE"


def test_chop_allows_atm_entry():
    s = select_structure("RANGE_BOUND_CHOP", "CE", iv_rank=40.0, spot=22000.0)
    assert s.trade is True
    assert s.legs[0].strike_offset == 0


def test_high_iv_blocks_long_premium():
    s = select_structure("TRENDING_BULLISH", "CE", iv_rank=80.0, spot=22000.0)
    assert s.trade is False



# --- Expiry day routing ---

def test_nifty_expiry_is_tuesday():
    expiry_map = {
        "NIFTY": {"weekly_weekday": 1, "monthly_weekday": 3},
        "BANKNIFTY": {"weekly_weekday": None, "monthly_weekday": 3},
        "DEFAULT": {"weekly_weekday": 3},
    }
    engine = PaperTradingEngine(
        risk_config={"total_capital": 100000, "max_open_positions": 2,
                     "confidence_floor": 60, "daily_drawdown_pause_pct": 5.0},
        skip_db_load=True,
        is_backtesting=True,
        expiry_map=expiry_map,
    )
    # Tuesday 2026-06-02 → weekday 1
    tuesday = datetime(2026, 6, 2, 15, 0)
    assert engine._is_expiry_day("NIFTY-Jun2026-22000-CE", tuesday)


def test_nifty_not_expiry_on_thursday():
    expiry_map = {
        "NIFTY": {"weekly_weekday": 1, "monthly_weekday": 3},
        "DEFAULT": {"weekly_weekday": 3},
    }
    engine = PaperTradingEngine(
        risk_config={"total_capital": 100000, "max_open_positions": 2,
                     "confidence_floor": 60, "daily_drawdown_pause_pct": 5.0},
        skip_db_load=True,
        is_backtesting=True,
        expiry_map=expiry_map,
    )
    # Thursday 2026-06-04 → weekday 3 — NOT NIFTY expiry
    thursday = datetime(2026, 6, 4, 15, 0)
    assert not engine._is_expiry_day("NIFTY-Jun2026-22000-CE", thursday)


# --- Pricing sanity: no synthetic price fabrication ---

def test_mark_to_market_skips_without_real_price():
    """MTM must not update position when no real option LTP exists."""
    import asyncio
    from datetime import datetime
    from services.chartedge_core.models import Candle, Direction, PaperTrade

    engine = PaperTradingEngine(
        risk_config={"total_capital": 100000, "max_open_positions": 2,
                     "confidence_floor": 60, "daily_drawdown_pause_pct": 5.0},
        skip_db_load=True,
        is_backtesting=False,
        costs_config={"enabled": False},
    )

    from uuid import uuid4
    trade = PaperTrade(
        signal_id=uuid4(),
        instrument="NIFTY-Jun2026-22000-CE",
        direction=Direction.BUY,
        entry_price=200.0,
        entry_time=datetime(2026, 6, 3, 10, 0),
        quantity=25,
        sl_price=170.0,
        t1_price=230.0,
        t2_price=260.0,
        invested_amount=5000.0,
        underlying_entry_price=22000.0,
    )
    engine.open_positions["NIFTY-Jun2026-22000-CE"] = trade
    original_pnl = trade.pnl

    # Candle is for NIFTY (underlying), not the option — no real option LTP in ltp_map
    candle = Candle(
        time=datetime(2026, 6, 3, 10, 5),
        instrument="NIFTY",
        timeframe="5m",
        open=22050.0, high=22060.0, low=22040.0, close=22055.0, volume=1000
    )

    asyncio.run(engine.mark_to_market(candle, ltp_map={"NIFTY": 22055.0}))

    # PnL must stay at original — no real option price, engine should not fabricate
    assert trade.pnl == original_pnl, (
        f"Engine fabricated PnL {trade.pnl} from underlying move. "
        "Should hold last known PnL when no real option LTP available."
    )

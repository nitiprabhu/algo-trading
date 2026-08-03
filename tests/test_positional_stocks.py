from datetime import date
from unittest.mock import patch

import pytest
from services.chartedge_core.positional_stocks import PositionalStocksEngine, StockPosition


@pytest.fixture
def clean_engine():
    with patch("services.chartedge_core.database.create_db_and_tables"), \
         patch("services.chartedge_core.database.get_open_stock_positions", return_value=[]), \
         patch("services.chartedge_core.database.get_closed_stock_positions", return_value=[]):
        engine = PositionalStocksEngine(
            capital=100000.0,
            max_positions=4,
            stop_loss_pct=4.0,
            target_pct=12.0,
            pool="test_pool",
            reentry_cooldown_sessions=2,
        )
        return engine


def test_cooldown_same_day(clean_engine):
    engine = clean_engine
    symbol = "M&MFIN"
    
    # Simulate a position exiting on Monday 2026-08-03
    exit_date = date(2026, 8, 3)
    engine.last_exit_dates[symbol] = exit_date

    # Same day should be blocked
    assert engine.is_in_cooldown(symbol, date(2026, 8, 3)) is True

    candidate = engine.build_entry_candidate(
        symbol=symbol,
        today=date(2026, 8, 3),
        price=405.0,
        score=0.6,
        buy_threshold=0.35,
        adx_value=30.0,
        trend_confirmed=True,
    )
    assert candidate is None


def test_cooldown_weekday_progression(clean_engine):
    engine = clean_engine
    symbol = "M&MFIN"
    
    # Exited Monday 2026-08-03
    engine.last_exit_dates[symbol] = date(2026, 8, 3)

    # Tuesday 2026-08-04 (1 trading session elapsed) -> Still in cooldown
    assert engine.is_in_cooldown(symbol, date(2026, 8, 4)) is True

    # Wednesday 2026-08-05 (2 trading sessions elapsed) -> Cooldown complete
    assert engine.is_in_cooldown(symbol, date(2026, 8, 5)) is False

    candidate = engine.build_entry_candidate(
        symbol=symbol,
        today=date(2026, 8, 5),
        price=410.0,
        score=0.6,
        buy_threshold=0.35,
        adx_value=30.0,
        trend_confirmed=True,
    )
    assert candidate is not None
    assert candidate.symbol == symbol


def test_cooldown_weekend_handling(clean_engine):
    engine = clean_engine
    symbol = "LAURUSLABS"
    
    # Exited Friday 2026-07-31
    engine.last_exit_dates[symbol] = date(2026, 7, 31)

    # Saturday 2026-08-01 -> Cooldown
    assert engine.is_in_cooldown(symbol, date(2026, 8, 1)) is True
    # Sunday 2026-08-02 -> Cooldown
    assert engine.is_in_cooldown(symbol, date(2026, 8, 2)) is True
    # Monday 2026-08-03 (1 trading session elapsed: Monday) -> Cooldown
    assert engine.is_in_cooldown(symbol, date(2026, 8, 3)) is True
    # Tuesday 2026-08-04 (2 trading sessions elapsed: Monday + Tuesday) -> Complete
    assert engine.is_in_cooldown(symbol, date(2026, 8, 4)) is False


def test_zero_cooldown_allows_immediate_reentry():
    with patch("services.chartedge_core.database.create_db_and_tables"), \
         patch("services.chartedge_core.database.get_open_stock_positions", return_value=[]), \
         patch("services.chartedge_core.database.get_closed_stock_positions", return_value=[]):
        engine = PositionalStocksEngine(
            capital=100000.0,
            max_positions=4,
            reentry_cooldown_sessions=0,
        )
        symbol = "TEST"
        engine.last_exit_dates[symbol] = date(2026, 8, 3)
        assert engine.is_in_cooldown(symbol, date(2026, 8, 3)) is False


def test_exit_records_cooldown(clean_engine):
    engine = clean_engine
    symbol = "M&MFIN"
    
    # Add open position
    pos = StockPosition(
        id="test-pos-1",
        symbol=symbol,
        entry_date="2026-07-29",
        entry_price=357.25,
        quantity=50,
    )
    engine.open_positions[symbol] = pos

    with patch("services.chartedge_core.database.persist_stock_exit"):
        # Price hits target (408.45 is +14.3% > 12.0%)
        closed = engine.check_exit(
            symbol=symbol,
            today=date(2026, 8, 3),
            price=408.45,
            score=0.5,
            sell_threshold=-0.35,
        )

    assert closed is not None
    assert closed.exit_reason == "TARGET"
    assert engine.last_exit_dates[symbol] == date(2026, 8, 3)
    assert engine.is_in_cooldown(symbol, date(2026, 8, 3)) is True

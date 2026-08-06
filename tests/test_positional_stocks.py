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


def test_exit_records_cooldown_on_full_exit(clean_engine):
    engine = clean_engine
    symbol = "M&MFIN"
    
    # Add open position with quantity=1 (full exit on target)
    pos = StockPosition(
        id="test-pos-1",
        symbol=symbol,
        entry_date="2026-07-29",
        entry_price=357.25,
        quantity=1,
    )
    engine.open_positions[symbol] = pos

    with patch("services.chartedge_core.database.persist_stock_exit"):
        # Price hits target (408.45 is +14.3% > 12.0%)
        event = engine.check_exit(
            symbol=symbol,
            today=date(2026, 8, 3),
            price=408.45,
            score=0.5,
            sell_threshold=-0.35,
        )

    assert event is not None
    assert event.is_partial is False
    assert event.exit_reason == "TARGET"
    assert engine.last_exit_dates[symbol] == date(2026, 8, 3)
    assert engine.is_in_cooldown(symbol, date(2026, 8, 3)) is True


def test_two_stage_exit_flow(clean_engine):
    engine = clean_engine
    engine.target_pct = 14.0
    symbol = "SBIN"

    # 1. Open position with 50 shares @ 800.0
    pos = StockPosition(
        id="pos-sbin-1",
        symbol=symbol,
        entry_date="2026-08-01",
        entry_price=800.0,
        quantity=50,
    )
    engine.open_positions[symbol] = pos

    # 2. Price reaches +14.5% (916.0) -> Triggers Stage 1 Partial Exit (50% = 25 shares)
    with patch("services.chartedge_core.database.persist_stock_partial_exit") as mock_partial_db:
        stage1_event = engine.check_exit(
            symbol=symbol,
            today=date(2026, 8, 10),
            price=916.0,
            score=0.6,
            sell_threshold=-0.35,
        )

    assert stage1_event is not None
    assert stage1_event.is_partial is True
    assert stage1_event.exit_qty == 25
    assert stage1_event.exit_price == 916.0
    assert stage1_event.exit_reason == "PARTIAL_TARGET_14PCT"
    assert stage1_event.pnl == (916.0 - 800.0) * 25  # 2900.0
    mock_partial_db.assert_called_once()

    # Verify position is still open with 25 runner shares
    assert symbol in engine.open_positions
    runner_pos = engine.open_positions[symbol]
    assert runner_pos.quantity == 25
    assert runner_pos.initial_quantity == 50
    assert runner_pos.partial_exit_done is True
    assert runner_pos.partial_pnl == 2900.0

    # 3. Runner reaches new high @ 960.0 (+20.0%) -> peak_pnl_pct updates to 20.0%
    no_exit = engine.check_exit(
        symbol=symbol,
        today=date(2026, 8, 12),
        price=960.0,
        score=0.5,
        sell_threshold=-0.35,
    )
    assert no_exit is None
    assert runner_pos.peak_pnl_pct == 20.0

    # 4. Price pulls back to +13.5% (908.0) -> below +14% locked floor -> Stage 2 triggers RUNNER_PROFIT_LOCK
    with patch("services.chartedge_core.database.persist_stock_exit") as mock_exit_db:
        stage2_event = engine.check_exit(
            symbol=symbol,
            today=date(2026, 8, 15),
            price=908.0,
            score=0.4,
            sell_threshold=-0.35,
        )

    assert stage2_event is not None
    assert stage2_event.is_partial is False
    assert stage2_event.exit_qty == 25
    assert stage2_event.exit_price == 908.0
    assert stage2_event.exit_reason == "RUNNER_PROFIT_LOCK"
    # Runner leg PnL = (908 - 800) * 25 = 2700.0
    assert stage2_event.pnl == 2700.0
    # Total trade PnL = 2900.0 (partial) + 2700.0 (runner) = 5600.0
    assert stage2_event.total_pnl == 5600.0
    mock_exit_db.assert_called_once()

    # Verify position is now closed
    assert symbol not in engine.open_positions
    assert len(engine.closed_positions) == 1
    assert engine.closed_positions[0].pnl == 5600.0
    assert engine.last_exit_dates[symbol] == date(2026, 8, 15)


def test_runner_higher_trailing_stop(clean_engine):
    engine = clean_engine
    engine.target_pct = 14.0
    symbol = "ADANIENT"

    pos = StockPosition(
        id="pos-adani-1",
        symbol=symbol,
        entry_date="2026-08-01",
        entry_price=1000.0,
        quantity=10,
    )
    engine.open_positions[symbol] = pos

    # Trigger Stage 1 partial exit @ 1150 (+15%)
    with patch("services.chartedge_core.database.persist_stock_partial_exit"):
        engine.check_exit(
            symbol=symbol,
            today=date(2026, 8, 5),
            price=1150.0,
            score=0.7,
            sell_threshold=-0.35,
        )

    runner_pos = engine.open_positions[symbol]
    assert runner_pos.quantity == 5
    assert runner_pos.partial_exit_done is True

    # Price zooms to +40% (1400.0) -> peak is 40%, 50% keep frac trailing floor is +20% (which > 14%)
    engine.check_exit(
        symbol=symbol,
        today=date(2026, 8, 10),
        price=1400.0,
        score=0.8,
        sell_threshold=-0.35,
    )
    assert runner_pos.peak_pnl_pct == 40.0

    # Price drops to +19% (1190.0) -> drops below trailing floor (+20%) -> triggers RUNNER_TRAILING_STOP
    with patch("services.chartedge_core.database.persist_stock_exit"):
        event = engine.check_exit(
            symbol=symbol,
            today=date(2026, 8, 12),
            price=1190.0,
            score=0.4,
            sell_threshold=-0.35,
        )

    assert event is not None
    assert event.is_partial is False
    assert event.exit_reason == "RUNNER_TRAILING_STOP"
    assert event.exit_price == 1190.0


def test_runner_protected_from_rotation(clean_engine):
    engine = clean_engine
    engine.target_pct = 14.0
    
    # Position in runner phase
    pos1 = StockPosition(
        id="pos-1",
        symbol="VEDL",
        entry_date="2026-08-01",
        entry_price=400.0,
        quantity=20,
        partial_exit_done=True,
        partial_pnl=1200.0,
        peak_pnl_pct=15.0,
    )
    engine.open_positions["VEDL"] = pos1

    # Attempt rotation
    rotated = engine.maybe_rotate_and_enter(
        symbol="ONGC",
        today=date(2026, 8, 10),
        price=300.0,
        score=0.85,
        buy_threshold=0.35,
        open_scores={"VEDL": 0.20},
        open_prices={"VEDL": 460.0},
    )
    # Runner position must NEVER be rotated out
    assert rotated is None

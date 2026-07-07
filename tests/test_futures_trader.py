import pytest
import asyncio
import uuid
from datetime import datetime, timezone
from services.chartedge_core.models import Signal, Direction, EntryZone, Candle, IndicatorSnapshot
from services.chartedge_core.futures_trader import FuturesTradingEngine

class MockTrader:
    def __init__(self):
        self.open_positions = {}
        self.risk_config = {}

class MockSimulator:
    def __init__(self):
        self.trader = MockTrader()
        
    def get_combined_daily_drawdown_pct(self, date):
        return 0.0

class MockPosition:
    def __init__(self, invested_amount):
        self.invested_amount = invested_amount

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
def futures_config():
    return {
        "NIFTY_FUT": {
            "lot_size": 75,
            "max_lots": 2,
            "sl_points": 50,
            "target_1_points": 75,
            "target_2_points": 150
        }
    }

@pytest.fixture
def risk_config():
    return {
        "notional_per_trade": 200000,
        "total_capital": 500000
    }

@pytest.fixture
def engine(futures_config, risk_config):
    e = FuturesTradingEngine(
        futures_risk_cfg=futures_config,
        is_backtesting=True,
        risk_config=risk_config
    )
    e.simulator = MockSimulator()
    return e

@pytest.fixture
def basic_signal():
    return Signal(
        id=uuid.uuid4(),
        created_at=datetime(2026, 6, 19, 10, 0, 0, tzinfo=timezone.utc),
        instrument="NIFTY_FUT",
        signal=Direction.BUY,
        confidence=80,
        entry_zone=EntryZone(low=22000.0, high=22100.0),
        stop_loss=21950.0,
        target_1=22150.0,
        target_2=22250.0,
        risk_reward_ratio=2.0,
        reasoning="Test",
        warnings=[],
        invalidation="Test",
        ai_model="TEST",
        indicator_snapshot=IndicatorSnapshot(
            instrument="NIFTY", timeframe="1m", candle_time=datetime(2026, 6, 19, 10, 0, 0, tzinfo=timezone.utc),
            price=22050.0, indicators={}, confluence_score=0.8
        )
    )

@pytest.fixture
def candle():
    return Candle(
        time=datetime(2026, 6, 19, 10, 0, 0, tzinfo=timezone.utc),
        instrument="NIFTY_FUT",
        timeframe="1m",
        open=22050.0,
        high=22060.0,
        low=22040.0,
        close=22055.0,
        volume=100
    )

@pytest.mark.anyio
async def test_mutual_exclusion_blocks_futures(engine, basic_signal, candle):
    # Enable mutual exclusion in config
    engine.risk_config["mutual_exclusion"] = True
    # Simulate an active options position
    engine.simulator.trader.open_positions["NIFTY_CE"] = MockPosition(invested_amount=10000)
    
    # Try to enter futures
    await engine.maybe_enter(basic_signal, candle)
    
    # Assert blocked
    assert len(engine.open_positions) == 0

@pytest.mark.anyio
async def test_successful_futures_entry(engine, basic_signal, candle):
    # No active options
    engine.simulator.trader.open_positions = {}
    
    # Try to enter futures
    await engine.maybe_enter(basic_signal, candle)
    
    # Assert entered successfully
    assert len(engine.open_positions) == 1
    assert "NIFTY_FUT" in engine.open_positions
    trade = engine.open_positions["NIFTY_FUT"]
    assert trade.direction == Direction.BUY
    assert trade.quantity > 0
    assert trade.quantity % 75 == 0  # Lot size check

@pytest.mark.anyio
async def test_eod_square_off(engine, basic_signal, candle):
    # Enter trade
    await engine.maybe_enter(basic_signal, candle)
    assert len(engine.open_positions) == 1
    
    # Trigger EOD
    eod_time = datetime.now(timezone.utc)
    await engine.force_close_all({"NIFTY_FUT": 22100.0}, eod_time, "EOD_SQUAREOFF")
    
    # Assert squared off
    assert len(engine.open_positions) == 0
    assert len(engine.closed_trades) == 1
    assert engine.closed_trades[0].exit_reason == "EOD_SQUAREOFF"

@pytest.mark.anyio
async def test_target_1_breakeven_trail(engine, basic_signal, candle):
    engine.max_lots = 2
    engine.risk_config["notional_per_trade"] = 400000 # Enough capital for 2 lots
    engine.risk_config["futures_risk_per_trade_pct"] = 1.5 # Enough risk budget for 2 lots at 50pt SL

    # Enter trade
    await engine.maybe_enter(basic_signal, candle)
    trade = engine.open_positions["NIFTY_FUT"]
    original_sl = trade.sl_price
    
    # Trigger T1 (breakeven trail)
    t1_candle = candle.model_copy()
    t1_candle.high = basic_signal.target_1 + 10 # Breach T1
    t1_candle.close = basic_signal.target_1 + 5
    
    await engine.mark_to_market(t1_candle)
    
    # Assert SL moved to breakeven, quantity remains unchanged
    assert "NIFTY_FUT" in engine.open_positions
    trade = engine.open_positions["NIFTY_FUT"]
    assert trade.quantity == 150 # Still 2 lots
    assert trade.t1_hit == True
    assert trade.sl_price == trade.entry_price
    assert trade.sl_price != original_sl
    
    # Trigger T2 (full close)
    t2_candle = candle.model_copy()
    t2_candle.high = basic_signal.target_2 + 10 # Breach T2
    t2_candle.close = basic_signal.target_2 + 5
    
    await engine.mark_to_market(t2_candle)
    
    # Assert fully closed
    assert len(engine.open_positions) == 0
    assert len(engine.closed_trades) == 1 # Only 1 trade record for futures
    assert engine.closed_trades[0].exit_reason == "T2"

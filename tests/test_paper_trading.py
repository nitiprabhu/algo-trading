from datetime import datetime
import asyncio
from zoneinfo import ZoneInfo

from services.chartedge_core.models import Candle, Direction, EntryZone, IndicatorSnapshot, Signal
from services.chartedge_core.paper_trading import PaperTradingEngine


def _signal(direction: Direction = Direction.BUY, confidence: int = 75) -> Signal:
    snapshot = IndicatorSnapshot(
        instrument="NIFTY",
        timeframe="1m",
        candle_time=datetime.now(ZoneInfo("Asia/Kolkata")),
        price=100.0,
        indicators={},
        confluence_score=0.8,
    )
    return Signal(
        created_at=snapshot.candle_time,
        instrument="NIFTY",
        signal=direction,
        confidence=confidence,
        entry_zone=EntryZone(low=99, high=101),
        stop_loss=95,
        target_1=105,
        target_2=110,
        risk_reward_ratio=1.8,
        reasoning="test",
        invalidation="test",
        indicator_snapshot=snapshot,
        ai_model="test",
    )


def _candle(open_price: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        time=datetime.now(ZoneInfo("Asia/Kolkata")),
        instrument="NIFTY",
        timeframe="1m",
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=1000,
    )


def test_confidence_floor_prevents_entry() -> None:
    engine = PaperTradingEngine({"confidence_floor": 60, "notional_per_trade": 100000, "max_open_positions": 2}, skip_db_load=True)

    async def run_test():
        res = await engine.maybe_enter(_signal(confidence=55), _candle(100, 101, 99, 100))
        assert res is None
        assert engine.open_positions == {}

    asyncio.run(run_test())


def test_t1_moves_stop_to_breakeven() -> None:
    engine = PaperTradingEngine({"confidence_floor": 60, "notional_per_trade": 100000, "max_open_positions": 2}, skip_db_load=True)

    async def run_test():
        trade = await engine.maybe_enter(_signal(), _candle(100, 101, 99, 100))
        assert trade is not None
        await engine.mark_to_market(_candle(104, 106, 103, 105))
        assert engine.open_positions["NIFTY"].t1_hit is True
        assert engine.open_positions["NIFTY"].sl_price == trade.entry_price

    asyncio.run(run_test())

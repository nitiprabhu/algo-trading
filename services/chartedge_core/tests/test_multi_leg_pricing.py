import pytest
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import sys
import os

# Ensure the parent directory is in the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from services.chartedge_core.models import LegExecution, Direction, PaperTrade, Signal, Candle
from services.chartedge_core.paper_trading import PaperTradingEngine
from services.chartedge_core.config import Config

# Mock config
class MockConfig:
    risk = {"max_open_positions": 5, "total_capital": 100000}

class MockTrader(PaperTradingEngine):
    def __init__(self):
        self.config = MockConfig()
        self.risk_config = self.config.risk
        self.open_positions = {}
        self.queued_signals = []
        self.blocked_directions = set()
        self.is_backtesting = True
        self.expiry_map = {"DEFAULT": {"type": "monthly", "day": 1}}
        self.costs_enabled = False
        self.closed_trades = []
        self.daily_pnl = 0.0

def test_multi_leg_mtm():
    trader = MockTrader()
    
    # Create a PaperTrade for an Iron Condor
    legs = [
        LegExecution(instrument="NIFTY_PE_SELL", action=Direction.SELL, ratio=1, entry_price=100.0, strike=24000, option_type="PE"),
        LegExecution(instrument="NIFTY_PE_BUY", action=Direction.BUY, ratio=1, entry_price=80.0, strike=23900, option_type="PE"),
        LegExecution(instrument="NIFTY_CE_SELL", action=Direction.SELL, ratio=1, entry_price=120.0, strike=24200, option_type="CE"),
        LegExecution(instrument="NIFTY_CE_BUY", action=Direction.BUY, ratio=1, entry_price=90.0, strike=24300, option_type="CE"),
    ]
    
    # Net entry price = (80 - 100) + (90 - 120) = -20 - 30 = -50 (credit of 50)
    import uuid
    trade = PaperTrade(
        signal_id=uuid.uuid4(),
        instrument="IRON_CONDOR:NIFTY",
        direction=Direction.BUY,
        entry_price=50.0,
        quantity=50,
        underlying_entry_price=24100.0,
        entry_time=datetime.now(),
        invested_amount=2500.0,
        sl_price=20.0,
        t1_price=80.0,
        t2_price=100.0,
        pnl=0.0,
        costs_paid=0.0,
        legs=legs
    )
    
    trader.open_positions[trade.instrument] = trade
    
    # Test MTM with ltp_map
    ltp_map = {
        "NIFTY_PE_SELL": 110.0, # Loss of 10
        "NIFTY_PE_BUY": 85.0,   # Profit of 5
        "NIFTY_CE_SELL": 105.0, # Profit of 15
        "NIFTY_CE_BUY": 80.0,   # Loss of 10
    }
    
    # Net value = (85 - 110) + (80 - 105) = -25 - 25 = -50 (we owe 50)
    # The magnitude of current price is 50. Wait, earlier entry price was -50.
    # So PnL is (entry_price - current_price) if direction=SELL? Wait, trade.direction is BUY!
    # A Credit spread should technically be a SELL trade, but we bought the structure for -50.
    # Let's just check if current_price becomes 50.
    
    import asyncio
    asyncio.run(trader.mark_to_market(
        Candle(time=datetime.now(), instrument="NIFTY", timeframe="1m", open=24100, high=24100, low=24100, close=24100, volume=0),
        ltp_map=ltp_map
    ))
    
    updated_trade = trader.open_positions[trade.instrument]
    # In MTM, net_price = (110 * 1 * -1) + (85 * 1 * 1) + (105 * 1 * -1) + (80 * 1 * 1) = -110 + 85 - 105 + 80 = -50.
    # absolute value = 50.
    # So current_price = 50. PnL depends on trader._pnl.
    # For a BUY trade: (current_price - entry_price) * quantity. (50 - 50) * 50 = 0.
    
    print(f"MTM complete. PnL: {updated_trade.pnl}")
    assert updated_trade.pnl == 0.0, f"Expected 0.0 PnL, got {updated_trade.pnl}"
    print("Test passed!")

if __name__ == '__main__':
    test_multi_leg_mtm()

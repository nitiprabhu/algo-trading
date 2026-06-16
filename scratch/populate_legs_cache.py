import os
from datetime import datetime
from sqlmodel import Session, select
from services.chartedge_core.database import TradeRecord, engine
from services.chartedge_core.training_logger import save_trade_legs_to_cache
from services.chartedge_core.derivative_manager import DerivativeManager
from services.chartedge_core.models import LegExecution, Direction

def populate():
    token = os.getenv("INDMONEY_TOKEN") or os.getenv("INDSTOCKS_TOKEN") or "DUMMY_TOKEN"
    dm = DerivativeManager(token)
    
    # Expiry is 23JUN26
    current_dt = datetime(2026, 6, 16, 10, 0, 0)
    
    with Session(engine) as session:
        # Fetch today's open trades
        trades = session.exec(
            select(TradeRecord).where(
                TradeRecord.symbol == "DEBIT_SPREAD:NIFTY_23JUN26",
                TradeRecord.status == "OPEN",
                TradeRecord.trade_date == "2026-06-16"
            )
        ).all()
        
        for t in trades:
            legs = []
            if abs(t.entry_price - 47.32) < 0.01:
                # Trade 1: PE 23950 (Buy) and 23850 (Sell)
                buy_strike = 23950.0
                sell_strike = 23850.0
                buy_entry = 186.95
                sell_entry = 139.65
            else:
                # Trade 2: PE 24000 (Buy) and 23900 (Sell)
                buy_strike = 24000.0
                sell_strike = 23900.0
                buy_entry = 210.10
                sell_entry = 159.45
                
            # Resolve tokens using DerivativeManager
            try:
                # Resolve Buy PE Leg
                buy_opts = dm.get_atm_options(buy_strike, "NIFTY", current_dt=current_dt, strike_offset=0)
                buy_contract = buy_opts.get("PE")
                
                # Resolve Sell PE Leg
                sell_opts = dm.get_atm_options(sell_strike, "NIFTY", current_dt=current_dt, strike_offset=0)
                sell_contract = sell_opts.get("PE")
                
                if buy_contract and sell_contract:
                    legs.append(LegExecution(
                        instrument=buy_contract["token"],
                        action=Direction.BUY,
                        ratio=1,
                        entry_price=buy_entry,
                        strike=buy_strike,
                        option_type="PE"
                    ))
                    legs.append(LegExecution(
                        instrument=sell_contract["token"],
                        action=Direction.SELL,
                        ratio=1,
                        entry_price=sell_entry,
                        strike=sell_strike,
                        option_type="PE"
                    ))
                    save_trade_legs_to_cache(t.trade_id, legs)
                    print(f"✅ Successfully cached legs for Trade ID {t.trade_id} (Entry: {t.entry_price})")
                else:
                    print(f"❌ Failed to resolve one of the contracts for entry {t.entry_price}")
            except Exception as e:
                print(f"❌ Error during resolution for entry {t.entry_price}: {e}")

if __name__ == "__main__":
    populate()

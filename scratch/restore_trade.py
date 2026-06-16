import json
import os
from datetime import datetime
from pathlib import Path
from sqlmodel import Session, SQLModel, create_engine, select
from services.chartedge_core.database import TradeRecord, engine
from services.chartedge_core.models import LegExecution, Direction
from services.chartedge_core.training_logger import save_trade_legs_to_cache

def restore():
    # 1. Insert/Update TradeRecord in PostgreSQL
    with Session(engine) as session:
        # Check if already exists
        existing = session.exec(
            select(TradeRecord).where(TradeRecord.trade_id == "7cc26fa5-7955-4a90-9eaa-011aa8ab568c")
        ).first()
        
        if existing:
            existing.status = "OPEN"
            session.add(existing)
            print("Updated existing trade status to OPEN in database.")
        else:
            record = TradeRecord(
                trade_id="7cc26fa5-7955-4a90-9eaa-011aa8ab568c",
                signal_id="1aa6bdf5-e6b3-4b47-a451-e86ab2d05526",
                symbol="DEBIT_SPREAD:NIFTY_23JUN26",
                direction="BUY",
                entry_price=48.12,
                entry_time=datetime(2026, 6, 16, 12, 30, 0),
                quantity=75,
                underlying_entry_price=23940.8,
                sl_price=24.06,
                t1_price=72.18,
                t2_price=96.24,
                status="OPEN",
                trade_date="2026-06-16",
                invested_amount=3609.0,
                pnl_pct=0.0
            )
            session.add(record)
            print("Inserted trade record as OPEN in database.")
            
        session.commit()

    # 2. Save legs to legs_cache.json
    legs = [
        LegExecution(
            instrument="NIFTY-Jun2026-23900-CE",
            action=Direction.BUY,
            ratio=1,
            entry_price=188.20,
            strike=23900.0,
            option_type="CE"
        ),
        LegExecution(
            instrument="NIFTY-Jun2026-24000-CE",
            action=Direction.SELL,
            ratio=1,
            entry_price=140.10,
            strike=24000.0,
            option_type="CE"
        )
    ]
    save_trade_legs_to_cache("7cc26fa5-7955-4a90-9eaa-011aa8ab568c", legs)
    print("Saved legs to cache successfully.")

if __name__ == "__main__":
    restore()

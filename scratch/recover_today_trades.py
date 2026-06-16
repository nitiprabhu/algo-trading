from datetime import datetime
from uuid import uuid4
from sqlmodel import Session, create_engine, select
from services.chartedge_core.database import TradeRecord, engine

def recover_trades():
    # Let's insert the two open trades of today into the database so the restarted server loads them.
    t1_id = str(uuid4())
    t2_id = str(uuid4())
    sig1_id = str(uuid4())
    sig2_id = str(uuid4())

    trade1 = TradeRecord(
        trade_id=t1_id,
        signal_id=sig1_id,
        symbol="DEBIT_SPREAD:NIFTY_23JUN26",
        direction="BUY",
        entry_price=47.32,
        entry_time=datetime(2026, 6, 16, 9, 45, 0),
        quantity=75,
        sl_price=23.66,
        t1_price=70.98,
        t2_price=94.64,
        status="OPEN",
        invested_amount=3549.00,
        underlying_entry_price=23900.0,
        trade_date="2026-06-16"
    )

    trade2 = TradeRecord(
        trade_id=t2_id,
        signal_id=sig2_id,
        symbol="DEBIT_SPREAD:NIFTY_23JUN26",
        direction="BUY",
        entry_price=50.68,
        entry_time=datetime(2026, 6, 16, 10, 44, 0),
        quantity=75,
        sl_price=25.34,
        t1_price=76.02,
        t2_price=101.36,
        status="OPEN",
        invested_amount=3801.00,
        underlying_entry_price=23950.0,
        trade_date="2026-06-16"
    )

    with Session(engine) as session:
        # Check if there are any existing open NIFTY trades for today first to avoid duplicates
        existing = session.exec(
            select(TradeRecord).where(
                TradeRecord.symbol == "DEBIT_SPREAD:NIFTY_23JUN26",
                TradeRecord.status == "OPEN",
                TradeRecord.trade_date == "2026-06-16"
            )
        ).all()
        for e in existing:
            session.delete(e)
        
        session.add(trade1)
        session.add(trade2)
        session.commit()
        print("✅ Inserted 2 recovered open trades into the local database.")

if __name__ == "__main__":
    recover_trades()

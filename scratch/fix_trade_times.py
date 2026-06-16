from datetime import datetime
from sqlmodel import Session, select
from services.chartedge_core.database import TradeRecord, engine

def fix_times():
    with Session(engine) as session:
        trades = session.exec(
            select(TradeRecord).where(
                TradeRecord.symbol == "DEBIT_SPREAD:NIFTY_23JUN26",
                TradeRecord.status == "OPEN",
                TradeRecord.trade_date == "2026-06-16"
            )
        ).all()
        
        for t in trades:
            if abs(t.entry_price - 47.32) < 0.01:
                # 9:45 AM IST -> 4:15 AM UTC
                t.entry_time = datetime(2026, 6, 16, 4, 15, 0)
            else:
                # 10:44 AM IST -> 5:14 AM UTC
                t.entry_time = datetime(2026, 6, 16, 5, 14, 0)
            session.add(t)
        session.commit()
        print("✅ Fixed trade entry times to UTC in database.")

if __name__ == "__main__":
    fix_times()

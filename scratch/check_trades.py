import os
import sys
from sqlmodel import create_engine, Session, select
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.chartedge_core.database import TradeRecord

load_dotenv()
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    print("No DATABASE_URL found.")
    sys.exit(1)

engine = create_engine(db_url)
try:
    with Session(engine) as session:
        today_date = "2026-06-16"
        trades = session.exec(
            select(TradeRecord).where(
                (TradeRecord.entry_time >= f"{today_date} 00:00:00") | 
                (TradeRecord.exit_time >= f"{today_date} 00:00:00")
            )
        ).all()
        
        if not trades:
            print("No trades found for today.")
        else:
            for t in trades:
                print(f"[{t.id}] {t.symbol} | {t.direction} | Entry: {t.entry_price} @ {t.entry_time} | Exit: {t.exit_price} @ {t.exit_time} | PnL: {t.pnl} | Status: {t.status}")
except Exception as e:
    print(f"Error querying database: {e}")

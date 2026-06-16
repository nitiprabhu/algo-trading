import os
import sys
from sqlmodel import create_engine, Session, select
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.chartedge_core.database import TradeRecord

load_dotenv()
db_url = os.environ.get("DATABASE_URL")
engine = create_engine(db_url)
with Session(engine) as session:
    t = session.get(TradeRecord, 6)
    if t:
        print(f"Trade 6: {t.symbol} | {t.direction} | Entry: {t.entry_price} @ {t.entry_time} | Status: {t.status} | SL: {t.sl_price} | T1: {t.t1_price} | Qty: {t.quantity}")
    else:
        print("Trade 6 not found.")

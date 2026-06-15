import os
from dotenv import load_dotenv
load_dotenv()
from sqlmodel import Session, create_engine, select
from services.chartedge_core.database import TradeRecord

engine = create_engine(os.getenv("DATABASE_URL"))
with Session(engine) as session:
    trades = session.exec(select(TradeRecord)).all()
    print(f"Total trades in DB: {len(trades)}")
    for t in trades:
        print(f"ID: {t.trade_id} | Symbol: {t.symbol} | Dir: {t.direction} | Status: {t.status} | Entry: {t.entry_price} | Exit: {t.exit_price} | PnL: {t.pnl:.2f}")

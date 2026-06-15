import os
import asyncio
from dotenv import load_dotenv
load_dotenv()

from sqlmodel import Session, create_engine, select
from services.chartedge_core.database import TradeRecord
from services.chartedge_core.telegram import notifier

async def main():
    engine = create_engine(os.getenv("DATABASE_URL"))
    with Session(engine) as session:
        trades = session.exec(select(TradeRecord).where(TradeRecord.status == "OPEN")).all()
        
    if not trades:
        msg = "📊 *ChartEdge Live Open Positions*\n\n📭 No open positions at the moment."
    else:
        msg = "📊 *ChartEdge Live Open Positions* 📊\n\n"
        for i, t in enumerate(trades, 1):
            msg += f"*{i}. Instrument*: `{t.symbol}`\n"
            msg += f"📈 *Direction*: {t.direction}\n"
            msg += f"💰 *Entry Price*: ₹{t.entry_price:.2f}\n"
            msg += f"📦 *Quantity*: {t.quantity}\n"
            msg += f"🛡️ *Status*: {t.status}\n\n"
            
    success = await notifier.send_message(msg)
    if success:
        print("Telegram notification sent successfully!")
    else:
        print("Failed to send Telegram notification.")

if __name__ == "__main__":
    asyncio.run(main())

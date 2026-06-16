import os
import sys
import asyncio
from datetime import datetime
from sqlmodel import create_engine, Session, select
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.chartedge_core.database import TradeRecord
from services.chartedge_core.telegram import notifier

load_dotenv()
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    print("No DATABASE_URL found.")
    sys.exit(1)

engine = create_engine(db_url)

with Session(engine) as session:
    # 1. Square off Trade 6
    trade = session.get(TradeRecord, 6)
    if trade and trade.status == "OPEN":
        trade.status = "CLOSED"
        trade.exit_price = trade.entry_price
        trade.exit_time = datetime.utcnow()
        trade.pnl = 0.0
        trade.pnl_pct = 0.0
        trade.exit_reason = "MARKET_CLOSED"
        session.add(trade)
        session.commit()
        print("Trade 6 squared off at entry price.")
    else:
        print("Trade 6 is already closed or not found.")
    
    # 2. Build final PnL summary
    today_date = "2026-06-16"
    trades = session.exec(
        select(TradeRecord).where(
            (TradeRecord.entry_time >= f"{today_date} 00:00:00") | 
            (TradeRecord.exit_time >= f"{today_date} 00:00:00")
        )
    ).all()
    
    realized_opts = 0.0
    realized_futs = 0.0
    unrealized_opts = 0.0
    unrealized_futs = 0.0
    
    for t in trades:
        # Check if option or future
        is_opt = "SPREAD" in t.symbol or "CE" in t.symbol or "PE" in t.symbol
        if is_opt:
            if t.status == "CLOSED":
                realized_opts += t.pnl
            else:
                unrealized_opts += t.pnl
        else:
            if t.status == "CLOSED":
                realized_futs += t.pnl
            else:
                unrealized_futs += t.pnl

    total_realized = realized_opts + realized_futs
    total_unrealized = unrealized_opts + unrealized_futs
    total_pnl = total_realized + total_unrealized
    
    ur_emoji = "🟢" if total_unrealized >= 0 else "🔴"
    re_emoji = "🟢" if total_realized >= 0 else "🔴"
    tot_emoji = "🟢" if total_pnl >= 0 else "🔴"
    
    msg = (
        f"💰 *PnL Summary:*\n\n"
        f"*Unrealized PnL:* {ur_emoji} `₹{total_unrealized:.2f}`\n"
        f"  \\- Options: `₹{unrealized_opts:.2f}`\n"
        f"  \\- Futures: `₹{unrealized_futs:.2f}`\n\n"
        f"*Realized PnL:* {re_emoji} `₹{total_realized:.2f}`\n"
        f"  \\- Options: `₹{realized_opts:.2f}`\n"
        f"  \\- Futures: `₹{realized_futs:.2f}`\n\n"
        f"*Total PnL:* {tot_emoji} `₹{total_pnl:.2f}`"
    )
    print("Generated Message:")
    print(msg)

async def send_msg():
    await notifier.resolve_chat_id()
    await notifier.send_message(msg)
    print("Notification sent.")

if __name__ == "__main__":
    asyncio.run(send_msg())

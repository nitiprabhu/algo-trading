import asyncio
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
os.environ.pop("ZERODHA_CACHE_DIR", None)

IST = ZoneInfo("Asia/Kolkata")

async def main():
    from services.chartedge_core.config import load_config
    from services.chartedge_core.indstocks import IndstocksMarketRuntime

    config = load_config()
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.signal_engine.ai_enabled = False

    start_date = datetime(2026, 6, 16, 9, 0, tzinfo=IST)
    end_date = datetime(2026, 6, 25, 15, 30, tzinfo=IST)

    print(f"\n>>> Running backtest from {start_date.date()} to {end_date.date()}...")
    result = await runtime.run_backtest(start_date, end_date, run_regime_agent=True)
    
    if result.get("status") != "ok":
        print(f"ERROR: Backtest failed. Result: {result}")
        return
        
    closed = runtime.trader.closed_trades + [
        t.to_paper_trade() for t in runtime.futures_trader.closed_trades
    ]
    
    opt_pnl = sum(t.pnl for t in runtime.trader.closed_trades)
    fut_pnl = sum(t.pnl for t in runtime.futures_trader.closed_trades)
    print(f"Options PnL: {opt_pnl:.2f}")
    print(f"Futures PnL: {fut_pnl:.2f}")
    print(f"Total PnL: {opt_pnl + fut_pnl:.2f}")
    print(f"Total Trades: {len(closed)}")
    print("\nDONE!")

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
os.environ.pop("ZERODHA_CACHE_DIR", None)

IST = ZoneInfo("Asia/Kolkata")

async def run_month(start_date, end_date):
    from services.chartedge_core.config import load_config
    from services.chartedge_core.indstocks import IndstocksMarketRuntime

    config = load_config()
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.signal_engine.ai_enabled = False

    print(f"\n{'='*60}")
    print(f">>> Running backtest from {start_date.date()} to {end_date.date()}...")
    result = await runtime.run_backtest(start_date, end_date, run_regime_agent=True)
    
    if result.get("status") != "ok":
        print(f"ERROR: Backtest failed. Result: {result}")
        return
        
    closed_opt = runtime.trader.closed_trades
    closed_fut = runtime.futures_trader.closed_trades
    
    opt_pnl = sum(t.pnl for t in closed_opt)
    fut_pnl = sum(t.pnl for t in closed_fut)
    print(f"\nResults for {start_date.strftime('%B %Y')} (up to {end_date.date()}):")
    print(f"Options PnL: ₹{opt_pnl:.2f}")
    print(f"Futures PnL: ₹{fut_pnl:.2f}")
    print(f"Total PnL: ₹{opt_pnl + fut_pnl:.2f}")
    print(f"Total Trades: {len(closed_opt) + len(closed_fut)}")
    print(f"{'='*60}\n")

async def main():
    jun_start = datetime(2026, 6, 1, 9, 0, tzinfo=IST)
    jun_end = datetime(2026, 6, 25, 15, 30, tzinfo=IST)
    await run_month(jun_start, jun_end)

    print("\nALL DONE!")

if __name__ == "__main__":
    asyncio.run(main())

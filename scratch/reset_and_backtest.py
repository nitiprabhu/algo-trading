import asyncio
import os
import sys
from datetime import datetime, timedelta

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.chartedge_core.database import create_db_and_tables
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

async def main():
    print("🧹 Database already migrated on Neon.")
    # create_db_and_tables() # Should be safe but not strictly needed if we already did it
    
    print("🚀 Starting fresh backtest for today...")
    config = load_config()
    
    # Force total_capital to 5L if not set, to match user's context
    config.risk["total_capital"] = 500000.0
    
    # We will PERSIST these backtest trades to DB so the user can see them on the dashboard
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    
    # Backtest for today
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = datetime.now()
    
    result = await runtime.run_backtest(today, end)
    print(f"✅ Backtest result: {result}")
    
    # Generate report
    closed = runtime.trader.closed_trades
    print("\n" + "="*80)
    print(f"{'INSTRUMENT':<25} | {'DIR':<4} | {'ENTRY':<8} | {'EXIT':<8} | {'PNL':<10} | {'PNL %':<8}")
    print("-" * 80)
    
    total_pnl = 0
    for t in sorted(closed, key=lambda x: x.entry_time):
        print(f"{t.instrument:<25} | {t.direction:<4} | {t.entry_price:<8.2f} | {t.exit_price:<8.2f} | {t.pnl:<10.2f} | {t.pnl_pct:<8.2f}%")
        total_pnl += t.pnl
        
    print("-" * 80)
    print(f"{'TOTAL PNL':<56} | {total_pnl:<10.2f}")
    print("="*80)

if __name__ == "__main__":
    asyncio.run(main())

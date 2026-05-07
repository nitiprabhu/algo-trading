import asyncio
import os
import json
from datetime import datetime, time
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime
from services.chartedge_core.simulation import IST

async def main():
    print("🚀 Initializing Direct Backtest Engine...")
    config = load_config()
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    
    # Target date: Today (May 5th)
    target_date = datetime.now(IST).date()
    start = datetime.combine(target_date, time(9, 30), IST)
    end = datetime.combine(target_date, time(15, 30), IST)
    
    print(f"📅 Period: {start} to {end}")
    
    # Run the backtest
    result = await runtime.run_backtest(start, end)
    
    if result.get("status") == "error":
        print(f"❌ Error: {result.get('reason')}")
        return
        
    # Get results from runtime state
    snapshot = runtime.snapshot()
    trades = [t for t in runtime.trader.closed_trades]
    metrics = snapshot.metrics
    
    print("\n" + "="*60)
    print(f"📊 DIRECT BACKTEST REPORT: {target_date}")
    print("="*60)
    print(f"💰 Realized PnL: {metrics.get('realized_pnl', 0):.2f}")
    print(f"📈 Win Rate: {metrics.get('win_rate', 0):.2f}%")
    print(f"🔄 Total Trades: {metrics.get('total_trades', 0)}")
    print("-" * 60)
    
    if not trades:
        print("No trades were taken today.")
    else:
        print(f"{'Time':<20} | {'Symbol':<12} | {'Side':<4} | {'Entry':<8} | {'Exit':<8} | {'PnL':<8}")
        print("-" * 80)
        for t in trades:
            # PaperTrade objects have attributes
            print(f"{str(t.entry_time)[:19]:<20} | {t.instrument:<12} | {t.direction.value:<4} | {t.entry_price:<8.2f} | {t.exit_price:<8.2f} | {t.pnl:<8.2f}")
    
    print("="*60)
    
    # Save results
    os.makedirs("reports", exist_ok=True)
    with open(f"reports/direct_backtest_{target_date}.json", "w") as f:
        json.dump({
            "metrics": metrics,
            "trades": [t.model_dump(mode="json") for t in trades]
        }, f, indent=4)

if __name__ == "__main__":
    asyncio.run(main())

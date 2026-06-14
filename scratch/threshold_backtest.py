import asyncio
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from tabulate import tabulate
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")

async def run_threshold_backtest(threshold, start_date, end_date):
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)
    
    # Set the confidence floor
    config.risk["confidence_floor"] = threshold
    # Disable AI for fast backtesting
    config.ai["enabled"] = False
    
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.trader.is_backtesting = True
    
    results = await runtime.run_backtest(start_date, end_date)
    
    if results.get("status") == "error":
        return None
        
    return runtime.trader.closed_trades, runtime.trader.metrics()

async def main():
    # Dates for this week: May 11 to May 15, 2026
    start = datetime(2026, 5, 11, 9, 15, tzinfo=IST)
    end = datetime(2026, 5, 15, 15, 30, tzinfo=IST)
    
    thresholds = [70, 60, 55, 50]
    
    all_results = []
    
    print(f"🚀 Starting Multi-Threshold Backtest Comparison")
    print(f"📅 Period: {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
    print(f"🔢 Thresholds to test: {thresholds}")
    print("-" * 50)

    for thresh in thresholds:
        print(f"🔄 Testing Confidence Threshold: {thresh}...")
        trades, metrics = await run_threshold_backtest(thresh, start, end)
        
        if trades is not None:
            all_results.append({
                "threshold": thresh,
                "trades_count": len(trades),
                "pnl": metrics["realized_pnl"],
                "win_rate": metrics["win_rate"],
                "profit_factor": metrics["profit_factor"]
            })
            print(f"✅ Completed: {len(trades)} trades, PnL: ₹{metrics['realized_pnl']:+,.2f}")
        else:
            print(f"❌ Failed to run backtest for threshold {thresh}")

    print("\n" + "=" * 80)
    print("📊 BACKTEST PERFORMANCE COMPARISON (MAY 11-15, 2026)")
    print("=" * 80)
    
    headers = ["Threshold", "Total Trades", "Net PnL (₹)", "Win Rate (%)", "Profit Factor"]
    table_data = []
    for res in all_results:
        table_data.append([
            res["threshold"],
            res["trades_count"],
            f"{res['pnl']:+,.2f}",
            f"{res['win_rate']:.1f}%",
            res["profit_factor"]
        ])
        
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    
    print("\n💡 OBSERVATION:")
    if not all_results:
        print("No results generated.")
    else:
        best = max(all_results, key=lambda x: x["pnl"])
        most_trades = max(all_results, key=lambda x: x["trades_count"])
        print(f"🏆 Best PnL: Threshold {best['threshold']} (₹{best['pnl']:+,.2f})")
        print(f"📈 Most Activity: Threshold {most_trades['threshold']} ({most_trades['trades_count']} trades)")

if __name__ == "__main__":
    asyncio.run(main())

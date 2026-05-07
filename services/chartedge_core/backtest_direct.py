import asyncio
import os
import json
import statistics
from datetime import datetime, timedelta
from dotenv import load_dotenv

from services.chartedge_core.indstocks import IndstocksMarketRuntime
from services.chartedge_core.config import load_config

load_dotenv()

async def run_direct_multi_day_backtest(start_date_str, end_date_str):
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
    
    config = load_config()
    runtime = IndstocksMarketRuntime(config)
    
    current_date = start_date
    all_metrics = []
    
    print(f"Starting DIRECT multi-day backtest from {start_date_str} to {end_date_str}...")
    
    while current_date <= end_date:
        if current_date.weekday() < 5:
            date_str = current_date.strftime("%Y-%m-%d")
            print(f"Running backtest for {date_str}...")
            
            # Set up backtest dates (9:15 to 15:30 IST)
            bt_start = current_date.replace(hour=9, minute=15, second=0, microsecond=0)
            bt_end = current_date.replace(hour=15, minute=5, second=0, microsecond=0)
            
            try:
                # Run backtest directly on runtime
                await runtime.run_backtest(bt_start, bt_end)
                
                # Extract metrics
                m = runtime.trader.metrics()
                metrics = {
                    "total_pnl": m["realized_pnl"],
                    "total_trades": int(m["total_trades"]),
                    "win_rate": m["win_rate"],
                }
                metrics["date"] = date_str
                all_metrics.append(metrics.copy())
                
                print(f"  Result: PnL={metrics.get('total_pnl', 0):.2f}, Trades={metrics.get('total_trades', 0)}, WinRate={metrics.get('win_rate', 0):.2f}%")
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"  Error for {date_str}: {e}")
        
        current_date += timedelta(days=1)
            
    return all_metrics

def analyze_performance(all_metrics):
    if not all_metrics:
        print("No metrics collected.")
        return None
    
    total_pnl = sum(m.get("total_pnl", 0) for m in all_metrics)
    total_trades = sum(m.get("total_trades", 0) for m in all_metrics)
    win_rates = [m.get("win_rate", 0) for m in all_metrics if m.get("total_trades", 0) > 0]
    avg_win_rate = statistics.mean(win_rates) if win_rates else 0
    
    print("\n--- Aggregate Performance Analysis (AI Verified) ---")
    print(f"Period: {all_metrics[0]['date']} to {all_metrics[-1]['date']}")
    print(f"Total PnL: {total_pnl:.2f}")
    print(f"Total Trades: {total_trades}")
    print(f"Avg Win Rate: {avg_win_rate:.2f}%")
    
    suggestions = []
    if avg_win_rate < 45:
        suggestions.append("Win rate is still below target. AI is filtering signals, but weighting may need more focus on trend.")
    
    return {
        "total_pnl": total_pnl,
        "total_trades": total_trades,
        "avg_win_rate": avg_win_rate,
        "suggestions": suggestions
    }

async def main():
    metrics = await run_direct_multi_day_backtest("2026-03-01", "2026-03-31")
    analysis = analyze_performance(metrics)
    
    if metrics:
        with open("backtest_analysis_ai.json", "w") as f:
            json.dump({"metrics": metrics, "analysis": analysis}, f, indent=4)
        print("\nResults saved to backtest_analysis_ai.json")

if __name__ == "__main__":
    asyncio.run(main())

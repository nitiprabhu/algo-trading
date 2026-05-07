import httpx
import asyncio
from datetime import datetime, timedelta
import json
import statistics

BASE_URL = "http://127.0.0.1:8000"

async def run_multi_day_backtest(start_date_str, end_date_str):
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
    
    current_date = start_date
    all_metrics = []
    
    print(f"Starting multi-day backtest from {start_date_str} to {end_date_str}...")
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        while current_date <= end_date:
            # Skip weekends for NSE
            if current_date.weekday() < 5:
                date_str = current_date.strftime("%Y-%m-%d")
                print(f"Running backtest for {date_str}...")
                
                try:
                    response = await client.post(f"{BASE_URL}/api/backtest?target_date={date_str}")
                    response.raise_for_status()
                    data = response.json()
                    
                    backtest_info = data.get("backtest", {})
                    if backtest_info.get("status") == "error":
                        print(f"  Backtest Error: {backtest_info}")
                        continue

                    snapshot = data.get("snapshot", {})
                    metrics = snapshot.get("metrics", {})
                    metrics["date"] = date_str
                    all_metrics.append(metrics)
                    
                    print(f"  Result: PnL={metrics.get('total_pnl', 0):.2f}, Trades={metrics.get('total_trades', 0)}")
                except Exception as e:
                    print(f"  Error for {date_str}: {e}")
            
            current_date += timedelta(days=1)
            
    return all_metrics

def analyze_performance(all_metrics):
    if not all_metrics:
        print("No metrics collected.")
        return
    
    total_pnl = sum(m.get("total_pnl", 0) for m in all_metrics)
    total_trades = sum(m.get("total_trades", 0) for m in all_metrics)
    win_rates = [m.get("win_rate", 0) for m in all_metrics if m.get("total_trades", 0) > 0]
    avg_win_rate = statistics.mean(win_rates) if win_rates else 0
    
    print("\n--- Aggregate Performance Analysis ---")
    print(f"Period: {all_metrics[0]['date']} to {all_metrics[-1]['date']}")
    print(f"Total PnL: {total_pnl:.2f}")
    print(f"Total Trades: {total_trades}")
    print(f"Avg Win Rate: {avg_win_rate:.2f}%")
    
    # Simple logic for threshold tuning
    # If win rate is < 40%, maybe tighten thresholds to be more selective (increase buy/sell threshold absolute values)
    # If win rate is > 60% but PnL is low, maybe loosen thresholds to capture more signals
    
    suggestions = []
    if avg_win_rate < 45:
        suggestions.append("Win rate is low. Suggest tightening thresholds (increase absolute values) to be more selective.")
    elif avg_win_rate > 55 and total_trades < 5:
        suggestions.append("Win rate is high but few trades. Suggest loosening thresholds slightly to capture more opportunity.")
    
    return {
        "total_pnl": total_pnl,
        "total_trades": total_trades,
        "avg_win_rate": avg_win_rate,
        "suggestions": suggestions
    }

async def main():
    metrics = await run_multi_day_backtest("2026-04-20", "2026-04-28")
    analysis = analyze_performance(metrics)
    
    with open("backtest_analysis.json", "w") as f:
        json.dump({"metrics": metrics, "analysis": analysis}, f, indent=4)

if __name__ == "__main__":
    asyncio.run(main())

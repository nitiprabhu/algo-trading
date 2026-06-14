import asyncio
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import pandas as pd

from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

load_dotenv()
IST = ZoneInfo("Asia/Kolkata")

async def main():
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)

    start_date = datetime(2026, 1, 1).date()
    end_date = datetime.now(IST).date()
    
    # Generate business days (Mon-Fri)
    days = pd.bdate_range(start=start_date, end=end_date)
    
    all_trades = []
    daily_results = []
    
    total_days = len(days)
    print(f"\n🚀 Starting full backtest for {total_days} trading days ({start_date} to {end_date})")
    print("Using REAL Indmoney Data & AI Consensus Debate. No mocks.")
    print("="*60)
    
    for i, date_obj in enumerate(days):
        date_str = date_obj.strftime("%Y-%m-%d")
        print(f"\n--- [{i+1}/{total_days}] Backtesting {date_str} ---")
        
        # New runtime per day to prevent state leakage
        runtime = IndstocksMarketRuntime(config, skip_db_load=True)
        runtime.signal_engine.ai_enabled = False
        async def mock_get_fo_signal(*args, **kwargs):
            return None
        runtime.signal_engine.get_fo_signal = mock_get_fo_signal
        
        runtime.signal_engine.thresholds = {"DEFAULT": 0.50, "NIFTY": 0.50, "BANKNIFTY": 0.50}
        
        target = date_obj.date()
        start = datetime.combine(target, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
        end   = datetime.combine(target, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)
        
        try:
            results = await runtime.run_backtest(start, end, run_regime_agent=False)
            
            if results.get("status") == "error":
                print(f"⚠️ Error/No Data on {date_str}: {results.get('reason')}")
                # Save as 0 trades if missing data
                daily_results.append({
                    "date": date_str,
                    "trades_count": 0,
                    "realized_pnl": 0.0,
                    "win_rate": 0.0,
                    "error": results.get('reason')
                })
                continue
                
            trades = runtime.trader.closed_trades
            m = runtime.trader.metrics()
            
            daily_pnl = m['realized_pnl']
            daily_trades = len(trades)
            
            print(f"✅ Day {date_str} -> Trades: {daily_trades}, PnL: ₹{daily_pnl:.2f}")
            
            day_result = {
                "date": date_str,
                "trades_count": daily_trades,
                "realized_pnl": daily_pnl,
                "win_rate": m.get('win_rate', 0.0),
                "threshold": runtime.signal_engine.thresholds.get('DEFAULT')
            }
            daily_results.append(day_result)
            
            for t in trades:
                all_trades.append({
                    "date": date_str,
                    "instrument": t.instrument,
                    "direction": t.direction.value,
                    "entry_time": t.entry_time.isoformat(),
                    "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "quantity": t.quantity,
                    "pnl": t.pnl,
                    "exit_reason": t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)
                })
                
        except Exception as e:
            print(f"❌ Exception on {date_str}: {e}")
            continue

    # Summary
    total_pnl = sum(d['realized_pnl'] for d in daily_results)
    total_trades = sum(d['trades_count'] for d in daily_results)
    winning_days = sum(1 for d in daily_results if d['realized_pnl'] > 0)
    losing_days = sum(1 for d in daily_results if d['realized_pnl'] < 0)
    
    print("\n" + "="*60)
    print(f"🎯 FINAL BACKTEST RESULTS (Jan 1 2026 - {end_date})")
    print(f"Total Days Processed: {len(daily_results)}")
    print(f"Winning Days:         {winning_days}")
    print(f"Losing Days:          {losing_days}")
    print(f"Total Trades:         {total_trades}")
    print(f"Total Realized PnL:   ₹{total_pnl:.2f}")
    print("="*60)
    
    # Save to disk
    report = {
        "summary": {
            "total_days": len(daily_results),
            "winning_days": winning_days,
            "losing_days": losing_days,
            "total_trades": total_trades,
            "total_realized_pnl": total_pnl
        },
        "daily_breakdown": daily_results,
        "all_trades": all_trades
    }
    
    with open("scratch/long_backtest_report.json", "w") as f:
        json.dump(report, f, indent=4)
        
    print("Report saved to scratch/long_backtest_report.json")

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import os
import sys
import argparse
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

# Load environment variables
load_dotenv()

IST = ZoneInfo("Asia/Kolkata")

async def main():
    parser = argparse.ArgumentParser(description="Run daily backtests sequentially across a month range.")
    parser.add_argument("--start", default="2026-05-01", help="Start date in YYYY-MM-DD format (default: 2026-05-01)")
    parser.add_argument("--end", default="2026-05-19", help="End date in YYYY-MM-DD format (default: 2026-05-19)")
    parser.add_argument("--fixed", type=float, help="Use a fixed threshold (disables AI Regime Agent)")
    
    args = parser.parse_args()
    
    start_date_str = args.start
    end_date_str = args.end
    fixed_threshold = args.fixed
        
    try:
        start_target = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_target = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except ValueError:
        print("❌ Invalid date format. Use YYYY-MM-DD.")
        sys.exit(1)

    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)

    # Initialize runtime
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)

    print(f"\n{'='*80}")
    print(f"🚀 INITIALIZING MONTHLY BACKTEST RUN ({start_date_str} to {end_date_str})")
    if fixed_threshold is not None:
        print(f"🎯 Dynamic AI Regime Agent: DISABLED")
        print(f"🎯 Fixed Confluence Threshold: {fixed_threshold:.2f}")
    else:
        print(f"🤖 Dynamic AI Regime Agent: ACTIVE")
    print(f"🧠 AI Signal Review for Trades: ENABLED")
    print(f"{'='*80}\n")

    all_trades = []
    daily_summaries = []
    
    current_date = start_target
    delta_day = timedelta(days=1)
    
    while current_date <= end_target:
        # Construct datetime bounds for checking trading day
        check_dt = datetime.combine(current_date, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
        
        if not runtime._is_trading_day(check_dt):
            print(f"📅 Skipping {current_date} (Weekend or NSE Holiday)")
            current_date += delta_day
            continue
            
        start_dt = datetime.combine(current_date, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
        end_dt   = datetime.combine(current_date, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)
        
        print(f"\n🔄 Running Backtest for {current_date}...")
        try:
            # Determine if we run with the regime agent or a fixed threshold
            run_agent = (fixed_threshold is None)
            if fixed_threshold is not None:
                # Set the threshold manually before the run
                for k in list(runtime.signal_engine.thresholds.keys()):
                    runtime.signal_engine.thresholds[k] = fixed_threshold
                runtime.signal_engine.thresholds["DEFAULT"] = fixed_threshold
                
            results = await runtime.run_backtest(start_dt, end_dt, run_regime_agent=run_agent)
            
            if results.get("status") == "error":
                print(f"⚠️ Error running backtest for {current_date}: {results.get('reason')}")
                current_date += delta_day
                continue
                
            # Capture daily trades and metadata
            day_trades = runtime.trader.closed_trades.copy()
            chosen_threshold = runtime.signal_engine.thresholds.get("DEFAULT", 0.50)
            
            # Since runtime.trader.metrics() is computed based on closed_trades of the day
            m = runtime.trader.metrics()
            daily_pnl = m["realized_pnl"]
            
            summary = {
                "date": current_date,
                "threshold": chosen_threshold,
                "trade_count": len(day_trades),
                "pnl": daily_pnl,
                "win_rate": m["win_rate"]
            }
            daily_summaries.append(summary)
            all_trades.extend(day_trades)
            
            print(f"✅ {current_date} Complete: Threshold={chosen_threshold:.2f} | Trades={len(day_trades)} | PnL=₹{daily_pnl:.2f}")
            
        except Exception as e:
            print(f"⚠️ Failed to run backtest for {current_date}: {e}")
            
        # Brief pause to be good API citizens
        await asyncio.sleep(1.0)
        current_date += delta_day

    # Compile aggregated stats
    total_trades = len(all_trades)
    total_pnl = sum(t.pnl for t in all_trades)
    
    wins = [t.pnl for t in all_trades if t.pnl > 0]
    losses = [t.pnl for t in all_trades if t.pnl <= 0]
    
    win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0.0
    
    sum_wins = sum(wins)
    sum_losses = abs(sum(losses))
    profit_factor = (sum_wins / sum_losses) if sum_losses > 0 else (sum_wins if sum_wins > 0 else 0.0)
    
    print(f"\n{'='*80}")
    print(f"📊 AGGREGATED MONTHLY BACKTEST PERFORMANCE METRICS")
    print(f"{'='*80}")
    print(f"Total Period:            {start_date_str} to {end_date_str}")
    print(f"Total Trading Days Run:  {len(daily_summaries)}")
    print(f"Total Executed Trades:   {total_trades}")
    print(f"Realized PnL:            ₹{total_pnl:.2f}")
    print(f"Win Rate:                {win_rate:.1f}% ({len(wins)} Wins, {len(losses)} Losses)")
    print(f"Profit Factor:           {profit_factor:.2f}")
    print(f"{'='*80}\n")
    
    if daily_summaries:
        print("📅 DAILY SUMMARY TABLE:")
        print("-" * 70)
        print(f"{'Date':12s} | {'Regime Threshold':18s} | {'Trade Count':12s} | {'PnL (₹)':12s} | {'Win Rate':8s}")
        print("-" * 70)
        for s in daily_summaries:
            print(f"{str(s['date']):12s} | {s['threshold']:<18.2f} | {s['trade_count']:<12d} | ₹{s['pnl']:<11.2f} | {s['win_rate']:<7.1f}%")
        print("-" * 70 + "\n")
        
    if all_trades:
        print("📝 DETAILED TRADE LIST:")
        print("-" * 130)
        print(f"{'Date':10s} | {'Entry':5s} | {'Exit':5s} | {'Instrument':26s} | {'Qty':5s} | {'Entry Px':10s} | {'Exit Px':10s} | {'PnL (₹)':12s} | {'PnL %':7s} | {'Reason'}")
        print("-" * 130)
        for t in all_trades:
            entry_t = t.entry_time.strftime('%H:%M')
            exit_t  = t.exit_time.strftime('%H:%M') if t.exit_time else "?"
            trade_date = t.entry_time.strftime('%Y-%m-%d')
            pnl_str = f"₹{t.pnl:.2f}"
            pnl_pct_str = f"{t.pnl_pct:.2f}%"
            print(f"{trade_date:10s} | {entry_t:5s} | {exit_t:5s} | {t.instrument:26s} | {t.quantity:<5d} | {t.entry_price:<10.2f} | {t.exit_price:<10.2f} | {pnl_str:12s} | {pnl_pct_str:7s} | {t.exit_reason}")
        print("-" * 130)

if __name__ == "__main__":
    asyncio.run(main())

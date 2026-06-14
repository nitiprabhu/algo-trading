import asyncio
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")
THRESHOLDS = [0.55, 0.50, 0.45, 0.40, 0.35, 0.30]

async def run_for_threshold(threshold: float, ai_enabled: bool = False):
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)
    
    # Configure threshold
    for key in list(config.confluence_thresholds.keys()):
        config.confluence_thresholds[key] = threshold
    config.confluence_thresholds["DEFAULT"] = threshold
    
    # Set AI review enabled or disabled
    config.ai["enabled"] = ai_enabled
    
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.trader.is_backtesting = True
    runtime.signal_engine.ai_enabled = ai_enabled
    for key in list(runtime.signal_engine.thresholds.keys()):
        runtime.signal_engine.thresholds[key] = threshold
    runtime.signal_engine.thresholds["DEFAULT"] = threshold

    target_date = datetime(2026, 5, 22).date()
    start = datetime.combine(target_date, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
    end = datetime.combine(target_date, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)
    
    # Silence prints during loop to keep output clean
    original_stdout = sys.stdout
    sys.stdout = open(os.devnull, 'w')
    try:
        await runtime.run_backtest(start, end)
    finally:
        sys.stdout.close()
        sys.stdout = original_stdout
        
    trades = runtime.trader.closed_trades
    metrics = runtime.trader.metrics()
    
    return {
        "threshold": threshold,
        "trades_count": len(trades),
        "pnl": metrics["realized_pnl"],
        "win_rate": metrics["win_rate"],
        "profit_factor": metrics.get("profit_factor", 0.0),
        "trades": [
            {
                "instrument": t.instrument,
                "entry_time": t.entry_time.strftime("%H:%M"),
                "exit_time": t.exit_time.strftime("%H:%M") if t.exit_time else "N/A",
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "exit_reason": t.exit_reason
            }
            for t in trades
        ]
    }

async def main():
    print("=========================================================================")
    print("📊 RUNNING BACKTESTS FOR MAY 22, 2026 WITH FIXED 5EMA STRATEGY")
    print("=========================================================================")
    print(f"{'Threshold':10s} | {'Trades':6s} | {'Net PnL (₹)':12s} | {'Win Rate':8s} | {'Profit Factor':13s}")
    print("-" * 65)
    
    results = []
    for thresh in THRESHOLDS:
        res = await run_for_threshold(thresh, ai_enabled=False)
        results.append(res)
        print(f"{res['threshold']:<10.2f} | {res['trades_count']:<6d} | ₹{res['pnl']:<11.2f} | {res['win_rate']:<7.1f}% | {res['profit_factor']:<13.2f}")
        
    # Write a detailed breakdown of trades for each threshold
    print("\n=========================================================================")
    print("📝 DETAILED TRADE BREAKDOWN BY THRESHOLD")
    print("=========================================================================")
    for res in results:
        print(f"\nConfluence Threshold: {res['threshold']:.2f} (Total Trades: {res['trades_count']})")
        print("-" * 80)
        if not res["trades"]:
            print("  No trades executed.")
            continue
        print(f"  {'Instrument':18s} | {'Entry':5s} | {'Exit':5s} | {'PnL (₹)':10s} | {'PnL %':6s} | {'Exit Reason':15s}")
        print("  " + "-" * 76)
        for t in res["trades"]:
            pnl_str = f"₹{t['pnl']:.2f}"
            pnl_pct_str = f"{t['pnl_pct']:.2f}%"
            print(f"  {t['instrument']:18s} | {t['entry_time']:5s} | {t['exit_time']:5s} | {pnl_str:10s} | {pnl_pct_str:6s} | {t['exit_reason']:15s}")

if __name__ == "__main__":
    asyncio.run(main())

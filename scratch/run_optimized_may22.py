import asyncio
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")

async def main():
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)
    
    # Ensure debate is enabled as per suggestion 3
    config.ai["debate_enabled"] = True
    config.ai["enabled"] = True
    
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.trader.is_backtesting = True
    
    target_date = datetime(2026, 5, 22).date()
    start = datetime.combine(target_date, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
    end = datetime.combine(target_date, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)
    
    print("🚀 Running optimized backtest for May 22, 2026...")
    print("Features active:")
    print("1. Asset-Specific dynamic thresholds determined by AI Regime Agent")
    print("2. 5EMA Mean Reversion Trend-Strength Guards (5m EMA9/EMA21 gap & 1m EMA ribbon alignment)")
    print("3. AI Consensus Debate enabled (Bull, Bear, Judge)\n")
    
    await runtime.run_backtest(start, end, run_regime_agent=True)
    
    trades = runtime.trader.closed_trades
    metrics = runtime.trader.metrics()
    
    print("\n=========================================================================")
    print("📊 OPTIMIZED BACKTEST RESULTS FOR MAY 22, 2026")
    print("=========================================================================")
    print(f"Total Trades: {len(trades)}")
    print(f"Realized PnL: ₹{metrics['realized_pnl']:.2f}")
    print(f"Win Rate: {metrics['win_rate']:.1f}%")
    print(f"Profit Factor: {metrics.get('profit_factor', 0.0):.2f}")
    
    print("\n📝 DETAILED TRADE LOG:")
    print("-" * 105)
    if not trades:
        print("No trades executed.")
    else:
        print(f"{'Instrument':18s} | {'Type':4s} | {'Entry':5s} | {'Exit':5s} | {'PnL (₹)':10s} | {'PnL %':6s} | {'Exit Reason':15s} | {'Strategy':10s}")
        print("-" * 105)
        for t in trades:
            matching_sig = next((s for s in runtime.signals if s.id == t.signal_id), None)
            strat = matching_sig.strategy_name if matching_sig else "CONFLUENCE"
            opt_type = matching_sig.option_type if matching_sig else "N/A"
            pnl_str = f"₹{t.pnl:.2f}"
            pnl_pct_str = f"{t.pnl_pct:.2f}%"
            print(f"{t.instrument:18s} | {opt_type:4s} | {t.entry_time.strftime('%H:%M'):5s} | {t.exit_time.strftime('%H:%M') if t.exit_time else 'N/A':5s} | {pnl_str:10s} | {pnl_pct_str:6s} | {t.exit_reason:15s} | {strat:10s}")

if __name__ == "__main__":
    asyncio.run(main())

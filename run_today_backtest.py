
import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from services.chartedge_core.config import Config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")

async def main():
    # Load config
    from services.chartedge_core.config import load_config
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)
    
    # Initialize runtime
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    
    # Define time range (Defaulting to May 8th for today's backtest)
    target_date = datetime(2026, 5, 8).date()
    start = datetime.combine(target_date, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
    end = datetime.combine(target_date, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)
    
    print(f"🚀 Starting Backtest for {target_date}")
    print(f"⏰ Range: {start} to {end}")
    print(f"📊 Timeframe: {config.risk.get('timeframe_mins')}m")
    
    # Run backtest
    results = await runtime.run_backtest(start, end, run_regime_agent=True)
    
    print("\n" + "="*50)
    print("🏁 BACKTEST RESULTS")
    print("="*50)
    print(f"Status: {results.get('status')}")
    for k, v in results.items():
        if k not in ["status"]:
            print(f"{k}: {v} candles processed")
    
    # Print trade summary
    print("\n📈 PERFORMANCE SUMMARY")
    opt_trades = len(runtime.trader.closed_trades)
    opt_pnl = sum(t.pnl for t in runtime.trader.closed_trades)
    print(f"Options Trades: {opt_trades} | Net PnL: ₹{opt_pnl:.2f}")
    
    fut_trades = len(runtime.futures_trader.closed_trades)
    fut_pnl = sum(t.pnl for t in runtime.futures_trader.closed_trades)
    print(f"Futures Trades: {fut_trades} | Net PnL: ₹{fut_pnl:.2f}")
    
    total_trades = opt_trades + fut_trades
    combined_pnl = opt_pnl + fut_pnl
    print(f"Combined Trades: {total_trades} | Combined Net PnL: ₹{combined_pnl:.2f}")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(main())

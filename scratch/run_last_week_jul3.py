import asyncio
import os
import sys
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime
from dotenv import load_dotenv

load_dotenv()

IST = ZoneInfo("Asia/Kolkata")

async def main():
    config = load_config("shared/config.yaml")
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.signal_engine.ai_enabled = False
    
    # Last week: June 29 to July 3, 2026
    start_date = datetime(2026, 6, 29, tzinfo=IST)
    end_date = datetime(2026, 7, 3, tzinfo=IST)
    
    current_date = start_date.date()
    delta_day = timedelta(days=1)
    
    print("\n" + "="*60)
    print("📊 LAST WEEK PERFORMANCE BREAKDOWN (Jun 29 - Jul 03, 2026)")
    print("="*60)
    print(f"{'Date':12s} | {'Options PnL':12s} | {'Futures PnL':12s} | {'Combined PnL'}")
    print("-" * 60)
    
    total_opt = 0.0
    total_fut = 0.0
    total_trades = 0
    
    while current_date <= end_date.date():
        start_dt = datetime.combine(current_date, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
        end_dt   = datetime.combine(current_date, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)
        
        if not runtime._is_trading_day(start_dt):
            current_date += delta_day
            continue
            
        await runtime.run_backtest(start_dt, end_dt, run_regime_agent=True)
        
        opt_trades = len(runtime.trader.closed_trades)
        fut_trades = len(runtime.futures_trader.closed_trades)
        
        opt_pnl = sum(t.pnl for t in runtime.trader.closed_trades)
        fut_pnl = sum(t.pnl for t in runtime.futures_trader.closed_trades)
        combined = opt_pnl + fut_pnl
        
        print(f"{str(current_date):12s} | ₹{opt_pnl:<11.2f} | ₹{fut_pnl:<11.2f} | ₹{combined:<11.2f}")
        
        total_opt += opt_pnl
        total_fut += fut_pnl
        total_trades += (opt_trades + fut_trades)
        
        # Reset trades for next day
        runtime.trader.closed_trades = []
        runtime.futures_trader.closed_trades = []
        
        current_date += delta_day

    print("-" * 60)
    print(f"{'TOTAL':12s} | ₹{total_opt:<11.2f} | ₹{total_fut:<11.2f} | ₹{(total_opt+total_fut):<11.2f}")
    print(f"Total Trades: {total_trades}")
    print("="*60 + "\n")

if __name__ == "__main__":
    asyncio.run(main())

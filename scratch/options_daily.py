import asyncio
import os
import sys
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")

async def main():
    config = load_config("shared/config.yaml")
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.signal_engine.ai_enabled = False
    
    start_date = datetime(2026, 6, 1, tzinfo=IST)
    end_date = datetime(2026, 6, 15, tzinfo=IST)
    
    current_date = start_date.date()
    delta_day = timedelta(days=1)
    
    print(f"{'Date':12s} | {'Options PnL':12s} | {'Futures PnL':12s}")
    print("-" * 45)
    
    while current_date <= end_date.date():
        start_dt = datetime.combine(current_date, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
        end_dt   = datetime.combine(current_date, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)
        
        if not runtime._is_trading_day(start_dt):
            current_date += delta_day
            continue
            
        await runtime.run_backtest(start_dt, end_dt, run_regime_agent=True)
        
        opt_pnl = sum(t.pnl for t in runtime.trader.closed_trades)
        fut_pnl = sum(t.pnl for t in runtime.futures_trader.closed_trades)
        
        print(f"{str(current_date):12s} | ₹{opt_pnl:<11.2f} | ₹{fut_pnl:<11.2f}")
        
        # Reset trades for next day
        runtime.trader.closed_trades = []
        runtime.futures_trader.closed_trades = []
        
        current_date += delta_day

if __name__ == "__main__":
    asyncio.run(main())

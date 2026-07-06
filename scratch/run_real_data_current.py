import asyncio
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
os.environ["ZERODHA_CACHE_DIR"] = "data/zerodha_cache"
os.environ["CHARTEDGE_DATA_SOURCE"] = "indstocks"  # use real INDMONEY_TOKEN from .env for option chain resolution

from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")
START = datetime(2026, 3, 2, tzinfo=IST)
END   = datetime(2026, 5, 19, tzinfo=IST)

async def main():
    config = load_config("shared/config.yaml")
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.signal_engine.ai_enabled = False  # match baseline methodology (rule-based)

    print("\n" + "=" * 70)
    print("📊 REAL ZERODHA DATA BACKTEST (current code + guardrails) — Mar 2 to May 19, 2026")
    print("=" * 70)
    print(f"{'Date':12s} | {'Options PnL':12s} | {'Futures PnL':12s} | {'Combined PnL':12s} | Trades")
    print("-" * 70)

    total_opt = 0.0
    total_fut = 0.0
    total_trades = 0
    current_date = START.date()
    delta_day = timedelta(days=1)

    while current_date <= END.date():
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
        day_trades = opt_trades + fut_trades

        print(f"{str(current_date):12s} | ₹{opt_pnl:<11.2f} | ₹{fut_pnl:<11.2f} | ₹{combined:<11.2f} | {day_trades}")

        total_opt += opt_pnl
        total_fut += fut_pnl
        total_trades += day_trades

        runtime.trader.closed_trades = []
        runtime.futures_trader.closed_trades = []

        current_date += delta_day

    print("-" * 70)
    print(f"{'TOTAL':12s} | ₹{total_opt:<11.2f} | ₹{total_fut:<11.2f} | ₹{(total_opt+total_fut):<11.2f} | {total_trades}")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    asyncio.run(main())

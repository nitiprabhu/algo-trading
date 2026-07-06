import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime
from dotenv import load_dotenv

load_dotenv()

IST = ZoneInfo("Asia/Kolkata")

START = datetime(2026, 4, 1, tzinfo=IST)
END   = datetime(2026, 4, 30, tzinfo=IST)

async def main():
    config = load_config("shared/config.yaml")
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.signal_engine.ai_enabled = False

    print("\n" + "=" * 70)
    print("📊 APR 2026 FULL MONTH BACKTEST (post-fix code)")
    print("=" * 70)
    print(f"{'Date':12s} | {'Options PnL':12s} | {'Futures PnL':12s} | {'Combined PnL':12s} | Trades")
    print("-" * 70)

    total_opt = 0.0
    total_fut = 0.0
    total_trades = 0
    week_opt = 0.0
    week_fut = 0.0
    week_trades = 0
    current_week = None

    current_date = START.date()
    delta_day = timedelta(days=1)

    while current_date <= END.date():
        start_dt = datetime.combine(current_date, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
        end_dt   = datetime.combine(current_date, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)

        if not runtime._is_trading_day(start_dt):
            current_date += delta_day
            continue

        iso_week = current_date.isocalendar()[1]
        if current_week is not None and iso_week != current_week:
            print("-" * 70)
            print(f"{'WEEK ' + str(current_week):12s} | ₹{week_opt:<11.2f} | ₹{week_fut:<11.2f} | ₹{(week_opt+week_fut):<11.2f} | {week_trades}")
            print("-" * 70)
            week_opt = 0.0
            week_fut = 0.0
            week_trades = 0
        current_week = iso_week

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
        week_opt += opt_pnl
        week_fut += fut_pnl
        week_trades += day_trades

        runtime.trader.closed_trades = []
        runtime.futures_trader.closed_trades = []

        current_date += delta_day

    print("-" * 70)
    print(f"{'WEEK ' + str(current_week):12s} | ₹{week_opt:<11.2f} | ₹{week_fut:<11.2f} | ₹{(week_opt+week_fut):<11.2f} | {week_trades}")
    print("=" * 70)
    print(f"{'MONTH TOTAL':12s} | ₹{total_opt:<11.2f} | ₹{total_fut:<11.2f} | ₹{(total_opt+total_fut):<11.2f} | {total_trades}")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    asyncio.run(main())

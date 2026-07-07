import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
os.environ["ZERODHA_CACHE_DIR"] = "data/zerodha_cache"
IST = ZoneInfo("Asia/Kolkata")

async def main():
    from services.chartedge_core.config import load_config
    from services.chartedge_core.indstocks import IndstocksMarketRuntime

    config = load_config()
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.signal_engine.ai_enabled = False

    # May 11 - May 15 is in the Zerodha cache
    start = datetime(2026, 5, 11, 9, 0, tzinfo=IST)
    end = datetime(2026, 5, 15, 15, 30, tzinfo=IST)

    print(f"Running backtest from {start.date()} to {end.date()} using Zerodha Cache...")
    result = await runtime.run_backtest(start, end, run_regime_agent=True)
    print("Result:", result)

    closed = runtime.trader.closed_trades + [
        t.to_paper_trade() for t in runtime.futures_trader.closed_trades
    ]
    print(f"Total trades: {len(closed)}")
    for t in closed:
        print(f"Trade: {t.instrument} {t.direction} at {t.entry_time} PnL: ₹{t.pnl:.2f} ({t.exit_reason})")

if __name__ == "__main__":
    asyncio.run(main())

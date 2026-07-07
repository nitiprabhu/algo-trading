import asyncio
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
os.environ.pop("ZERODHA_CACHE_DIR", None)

IST = ZoneInfo("Asia/Kolkata")

async def run_backtest_for_range(start_date, end_date) -> dict:
    from services.chartedge_core.config import load_config
    from services.chartedge_core.indstocks import IndstocksMarketRuntime

    config = load_config()
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.signal_engine.ai_enabled = False

    print(f">>> Running backtest from {start_date.date()} to {end_date.date()} (INDmoney, AI off, regime agent on)...")
    result = await runtime.run_backtest(start_date, end_date, run_regime_agent=True)
    
    if result.get("status") != "ok":
        print(f"ERROR: {result}")
        return {"error": result.get("reason", "Unknown error")}

    opt_pnl = sum(t.pnl for t in runtime.trader.closed_trades)
    fut_pnl = sum(t.pnl for t in runtime.futures_trader.closed_trades)
    combined = opt_pnl + fut_pnl
    trades = len(runtime.trader.closed_trades) + len(runtime.futures_trader.closed_trades)
    closed = runtime.trader.closed_trades + [
        t.to_paper_trade() for t in runtime.futures_trader.closed_trades
    ]
    wins = sum(1 for t in closed if t.pnl > 0)
    win_pct = round(wins / trades * 100, 1) if trades else 0.0

    return {
        "options": round(opt_pnl, 2),
        "futures": round(fut_pnl, 2),
        "combined": round(combined, 2),
        "trades": trades,
        "win_pct": win_pct,
    }

async def main():
    jan_start = datetime(2026, 1, 1, tzinfo=IST)
    jan_end = datetime(2026, 1, 31, 15, 30, tzinfo=IST)
    
    res = await run_backtest_for_range(jan_start, jan_end)
    
    msg = (
        "📊 *Backtest Performance Report*\n"
        "_(Regime Agent: ON | AI Review: OFF)_\n\n"
        "*📅 January 2026 Range (01 Jan - 31 Jan):*\n"
        f"  - Options PnL: `₹{res.get('options', 0.0):+,.2f}`\n"
        f"  - Futures PnL: `₹{res.get('futures', 0.0):+,.2f}`\n"
        f"  - Combined PnL: `₹{res.get('combined', 0.0):+,.2f}`\n"
        f"  - Total Trades: `{res.get('trades', 0)}` | Win Rate: `{res.get('win_pct', 0.0)}%`\n\n"
        "✅ Backtest completed successfully!"
    )
    
    print("\n" + "="*50)
    print(msg)
    print("="*50 + "\n")

if __name__ == "__main__":
    asyncio.run(main())

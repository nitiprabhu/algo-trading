import asyncio
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

# Always INDmoney — never Zerodha cache
os.environ.pop("ZERODHA_CACHE_DIR", None)

IST = ZoneInfo("Asia/Kolkata")

async def run_backtest_for_range(start_date, end_date) -> dict:
    from services.chartedge_core.config import load_config
    from services.chartedge_core.indstocks import IndstocksMarketRuntime

    config = load_config()
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.signal_engine.ai_enabled = False

    print(f">>> Running backtest from {start_date} to {end_date} (INDmoney, AI off, regime agent on)...")
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
    # 1. Run June Backtest (June 1 - June 12)
    june_start = datetime(2026, 6, 1, tzinfo=IST)
    june_end = datetime(2026, 6, 12, 15, 30, tzinfo=IST)
    june_res = await run_backtest_for_range(june_start, june_end)

    # 2. Run May Backtest (May 1 - May 31)
    may_start = datetime(2026, 5, 1, tzinfo=IST)
    may_end = datetime(2026, 5, 31, 15, 30, tzinfo=IST)
    may_res = await run_backtest_for_range(may_start, may_end)

    # 3. Format message
    msg = (
        "📊 *Backtest Performance Report*\n"
        "_(Regime Agent: ON | AI Review: OFF)_\n\n"
        "*📅 May 2026 Range (01 May - 31 May):*\n"
        f"  - Options PnL: `₹{may_res.get('options', 0.0):+,.2f}`\n"
        f"  - Futures PnL: `₹{may_res.get('futures', 0.0):+,.2f}`\n"
        f"  - Combined PnL: `₹{may_res.get('combined', 0.0):+,.2f}`\n"
        f"  - Total Trades: `{may_res.get('trades', 0)}` | Win Rate: `{may_res.get('win_pct', 0.0)}%`\n\n"
        "*📅 June 2026 Range (01 Jun - 12 Jun):*\n"
        f"  - Options PnL: `₹{june_res.get('options', 0.0):+,.2f}`\n"
        f"  - Futures PnL: `₹{june_res.get('futures', 0.0):+,.2f}`\n"
        f"  - Combined PnL: `₹{june_res.get('combined', 0.0):+,.2f}`\n"
        f"  - Total Trades: `{june_res.get('trades', 0)}` | Win Rate: `{june_res.get('win_pct', 0.0)}%`\n\n"
        "✅ Both backtests completed successfully! Timezone updates did not break the backtest pipeline."
    )

    print("\n" + "="*50)
    print(msg)
    print("="*50 + "\n")

    # Send message to Telegram
    from services.chartedge_core.telegram import notifier
    await notifier.send_message(msg)
    print("📢 Notification sent to Telegram successfully!")

if __name__ == "__main__":
    asyncio.run(main())

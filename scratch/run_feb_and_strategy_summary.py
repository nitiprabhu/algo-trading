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

    print(f">>> Running backtest from {start_date.date()} to {end_date.date()}...")
    result = await runtime.run_backtest(start_date, end_date, run_regime_agent=True)
    if result.get("status") != "ok":
        print(f"ERROR: {result}")
        return {"error": result.get("reason", "Unknown error")}

    closed = runtime.trader.closed_trades + [
        t.to_paper_trade() for t in runtime.futures_trader.closed_trades
    ]
    
    # Strategy breakdown
    strategy_pnl = {}
    strategy_trades = {}
    strategy_wins = {}

    for t in closed:
        # Try to infer strategy from instrument or exit reason if not explicitly set
        strat = getattr(t, "strategy_name", None)
        if not strat:
            if "CONDOR" in t.instrument:
                strat = "IRON_CONDOR"
            elif "FUT_" in t.instrument or "FUT" in getattr(t, "exit_reason", ""):
                # E.g. NIFTY_FUT_30JUN26 -> FUT
                strat = "FUTURES"
            elif "T315" in t.instrument:
                strat = "T315"
            else:
                strat = "OTHER_OPTIONS"
                
        # Group FUT_MORNING, FUT_MIDDAY into FUTURES
        if strat.startswith("FUT_"):
            strat = "FUTURES"
            
        if strat not in strategy_pnl:
            strategy_pnl[strat] = 0.0
            strategy_trades[strat] = 0
            strategy_wins[strat] = 0
            
        strategy_pnl[strat] += t.pnl
        strategy_trades[strat] += 1
        if t.pnl > 0:
            strategy_wins[strat] += 1

    opt_pnl = sum(t.pnl for t in runtime.trader.closed_trades)
    fut_pnl = sum(t.pnl for t in runtime.futures_trader.closed_trades)
    combined = opt_pnl + fut_pnl
    trades = len(closed)
    wins = sum(1 for t in closed if t.pnl > 0)
    win_pct = round(wins / trades * 100, 1) if trades else 0.0

    strat_summary = {}
    for strat in strategy_pnl:
        st_trades = strategy_trades[strat]
        st_win_pct = round(strategy_wins[strat] / st_trades * 100, 1) if st_trades else 0.0
        strat_summary[strat] = {
            "pnl": round(strategy_pnl[strat], 2),
            "trades": st_trades,
            "win_pct": st_win_pct
        }

    return {
        "options": round(opt_pnl, 2),
        "futures": round(fut_pnl, 2),
        "combined": round(combined, 2),
        "trades": trades,
        "win_pct": win_pct,
        "strategy_summary": strat_summary
    }

def format_month_res(name, res):
    if "error" in res:
        return f"*📅 {name}:* ERROR: {res['error']}\n\n"
    
    msg = (
        f"*📅 {name}:*\n"
        f"  - Combined PnL: `₹{res['combined']:+,.2f}` (Opts: `₹{res['options']:+,.2f}`, Futs: `₹{res['futures']:+,.2f}`)\n"
        f"  - Total Trades: `{res['trades']}` | Win Rate: `{res['win_pct']}%`\n"
        f"  - *Strategy Breakdown:*\n"
    )
    for strat, data in sorted(res.get("strategy_summary", {}).items(), key=lambda x: x[1]['pnl'], reverse=True):
        msg += f"      • {strat:12}: `₹{data['pnl']:>+9,.2f}` | {data['trades']:>3} trades ({data['win_pct']:>4}% WR)\n"
    return msg + "\n"

async def main():
    months = [
        ("February 2026", datetime(2026, 2, 1, tzinfo=IST), datetime(2026, 2, 28, 15, 30, tzinfo=IST)),
        ("March 2026",    datetime(2026, 3, 1, tzinfo=IST), datetime(2026, 3, 31, 15, 30, tzinfo=IST)),
        ("April 2026",    datetime(2026, 4, 1, tzinfo=IST), datetime(2026, 4, 30, 15, 30, tzinfo=IST)),
        ("May 2026",      datetime(2026, 5, 1, tzinfo=IST), datetime(2026, 5, 31, 15, 30, tzinfo=IST)),
        ("June 2026",     datetime(2026, 6, 1, tzinfo=IST), datetime(2026, 6, 15, 15, 30, tzinfo=IST)),
    ]

    print("\n" + "="*70)
    print("  RUNNING BACKTESTS: FEB 2026 - JUN 2026")
    print("="*70)

    results = {}
    for name, start, end in months:
        results[name] = await run_backtest_for_range(start, end)

    # Format message
    msg = "📊 *Comprehensive Strategy & Month Performance Report*\n\n"
    for name, _, _ in months:
        msg += format_month_res(name, results[name])
        
    msg += "✅ All backtests completed successfully!"

    print("\n" + "="*70)
    print(msg)
    print("="*70 + "\n")

    # Send message to Telegram
    try:
        from services.chartedge_core.telegram import notifier
        await notifier.send_message(msg)
        print("📢 Notification sent to Telegram successfully!")
    except Exception as e:
        print(f"⚠️ Failed to send Telegram notification: {e}")

if __name__ == "__main__":
    asyncio.run(main())

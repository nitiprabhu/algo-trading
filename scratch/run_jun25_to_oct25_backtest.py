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

    print(f"\n>>> Running backtest from {start_date.date()} to {end_date.date()}...")
    result = await runtime.run_backtest(start_date, end_date, run_regime_agent=True)
    
    if result.get("status") != "ok":
        print(f"ERROR: Backtest failed. Result: {result}")
        return {"status": "error", "reason": result.get("reason", "Unknown error")}

    # Validate that we actually got real candle data
    nifty_count = result.get("NIFTY", 0)
    bn_count = result.get("BANKNIFTY", 0)
    print(f"DEBUG: Fetched counts: NIFTY={nifty_count}, BANKNIFTY={bn_count}")
    
    if nifty_count == 0 or bn_count == 0:
        reason = f"No candle data available (NIFTY: {nifty_count}, BANKNIFTY: {bn_count})"
        print(f"STOPPING: {reason}")
        return {"status": "error", "reason": reason}

    closed = runtime.trader.closed_trades + [
        t.to_paper_trade() for t in runtime.futures_trader.closed_trades
    ]
    
    # Strategy breakdown
    strategy_pnl = {}
    strategy_trades = {}
    strategy_wins = {}

    for t in closed:
        strat = getattr(t, "strategy_name", None)
        if not strat:
            if "CONDOR" in t.instrument:
                strat = "IRON_CONDOR"
            elif "FUT_" in t.instrument or "FUT" in getattr(t, "exit_reason", ""):
                strat = "FUTURES"
            elif "T315" in t.instrument:
                strat = "T315"
            else:
                strat = "OTHER_OPTIONS"
                
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
        "status": "ok",
        "options": round(opt_pnl, 2),
        "futures": round(fut_pnl, 2),
        "combined": round(combined, 2),
        "trades": trades,
        "win_pct": win_pct,
        "strategy_summary": strat_summary
    }

def format_month_res(name, res):
    if res.get("status") != "ok":
        return f"📅 *{name}:* 🛑 STOPPED (Reason: {res.get('reason', 'Data unavailable')})\n\n"
    
    msg = (
        f"📅 *{name}:*\n"
        f"  - Combined PnL: `₹{res['combined']:+,.2f}` (Opts: `₹{res['options']:+,.2f}`, Futs: `₹{res['futures']:+,.2f}`)\n"
        f"  - Total Trades: `{res['trades']}` | Win Rate: `{res['win_pct']}%`\n"
        f"  - *Strategy Breakdown:*\n"
    )
    for strat, data in sorted(res.get("strategy_summary", {}).items(), key=lambda x: x[1]['pnl'], reverse=True):
        msg += f"      • {strat:12}: `₹{data['pnl']:>+9,.2f}` | {data['trades']:>3} trades ({data['win_pct']:>4}% WR)\n"
    return msg + "\n"

async def main():
    months = [
        ("June 2025",      datetime(2025, 6, 1, tzinfo=IST), datetime(2025, 6, 30, 15, 30, tzinfo=IST)),
        ("July 2025",      datetime(2025, 7, 1, tzinfo=IST), datetime(2025, 7, 31, 15, 30, tzinfo=IST)),
        ("August 2025",    datetime(2025, 8, 1, tzinfo=IST), datetime(2025, 8, 31, 15, 30, tzinfo=IST)),
        ("September 2025", datetime(2025, 9, 1, tzinfo=IST), datetime(2025, 9, 30, 15, 30, tzinfo=IST)),
        ("October 2025",   datetime(2025, 10, 1, tzinfo=IST), datetime(2025, 10, 31, 15, 30, tzinfo=IST)),
    ]

    print("\n" + "="*70)
    print("  RUNNING BACKTESTS: JUN 2025 - OCT 2025 (WITH DATA INTEGRITY CHECKS)")
    print("="*70)

    results = {}
    stopped_at = None
    
    for name, start, end in months:
        res = await run_backtest_for_range(start, end)
        results[name] = res
        if res.get("status") != "ok":
            stopped_at = name
            print(f"🛑 Stopping backtest execution flow at {name} due to missing data.")
            break

    # Format message
    msg = "📊 *Jun 2025 - Oct 2025 Backtest Performance Report*\n\n"
    for name, _, _ in months:
        if name in results:
            msg += format_month_res(name, results[name])
        else:
            msg += f"📅 *{name}:* ⏭️ Skipped (due to previous stoppage)\n\n"
            
    if stopped_at:
        msg += f"⚠️ **Execution stopped early at {stopped_at}** because real data was not available."
    else:
        msg += "✅ All requested backtests (Jun 2025 - Oct 2025) completed successfully!"

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

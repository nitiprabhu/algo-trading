import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
os.environ.pop("ZERODHA_CACHE_DIR", None)
IST = ZoneInfo("Asia/Kolkata")

async def main():
    from services.chartedge_core.config import load_config
    from services.chartedge_core.indstocks import IndstocksMarketRuntime

    config = load_config()
    # Ensure standard production default: monitors are monitor role
    for inst in config.instruments:
        if inst["symbol"] in ("RELIANCE", "HDFCBANK"):
            inst["role"] = "monitor"
    if "RELIANCE" in config.trading_symbols:
        config.trading_symbols.remove("RELIANCE")
    if "HDFCBANK" in config.trading_symbols:
        config.trading_symbols.remove("HDFCBANK")

    # Initialize runtime
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.signal_engine.ai_enabled = False  # Confluence-based signals, but Regime Agent ON

    start_date = datetime(2025, 11, 1, 9, 0, tzinfo=IST)
    end_date = datetime(2026, 2, 28, 15, 30, tzinfo=IST)

    print(f"\n📊 Starting continuous regime backtest from {start_date.date()} to {end_date.date()}...")
    results = await runtime.run_backtest(start_date, end_date, run_regime_agent=True)

    if results.get("status") == "error":
        print(f"🛑 Backtest execution failed: {results.get('reason')}")
        return

    # Combine options and futures trades
    all_trades = runtime.trader.closed_trades.copy() + [
        t.to_paper_trade() for t in runtime.futures_trader.closed_trades
    ]

    # Map strategies and set strategy_name for futures correctly
    for t in all_trades:
        if t.instrument == "NIFTY_FUT":
            t.strategy_name = "FUTURES"
        elif not t.strategy_name or t.strategy_name == "NAKED_BUY":
            # options structures could be ITM buy or condor
            if "CONDOR" in t.instrument:
                t.strategy_name = "IRON_CONDOR"
            elif "T315" in t.instrument:
                t.strategy_name = "T315"
            else:
                t.strategy_name = "NAKED_BUY"

    # Group trades by month
    monthly_data = {
        "November 2025": {"options": 0.0, "futures": 0.0, "trades": [], "win_rate": 0.0},
        "December 2025": {"options": 0.0, "futures": 0.0, "trades": [], "win_rate": 0.0},
        "January 2026": {"options": 0.0, "futures": 0.0, "trades": [], "win_rate": 0.0},
        "February 2026": {"options": 0.0, "futures": 0.0, "trades": [], "win_rate": 0.0},
    }

    for t in all_trades:
        trade_date = t.exit_time or t.entry_time
        if not trade_date:
            continue
        month_name = trade_date.strftime("%B %Y")
        if month_name in monthly_data:
            monthly_data[month_name]["trades"].append(t)
            if t.instrument == "NIFTY_FUT":
                monthly_data[month_name]["futures"] += t.pnl
            else:
                monthly_data[month_name]["options"] += t.pnl

    # Generate the report
    msg = "📊 *Nov 2025 - Feb 2026 Backtest Performance Report (Regime Agent: ON)*\n\n"
    
    total_opt = 0.0
    total_fut = 0.0
    total_trades_count = 0
    total_wins = 0

    for name, data in monthly_data.items():
        trades = data["trades"]
        num_trades = len(trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        win_pct = round((wins / num_trades) * 100, 1) if num_trades > 0 else 0.0
        
        opt_pnl = data["options"]
        fut_pnl = data["futures"]
        combined = opt_pnl + fut_pnl
        
        total_opt += opt_pnl
        total_fut += fut_pnl
        total_trades_count += num_trades
        total_wins += wins

        # Calculate strategy breakdown for this month
        strat_breakdown = {}
        for t in trades:
            strat = t.strategy_name
            if strat not in strat_breakdown:
                strat_breakdown[strat] = {"pnl": 0.0, "trades": 0, "wins": 0}
            strat_breakdown[strat]["pnl"] += t.pnl
            strat_breakdown[strat]["trades"] += 1
            if t.pnl > 0:
                strat_breakdown[strat]["wins"] += 1

        msg += (
            f"📅 *{name}:*\n"
            f"  - Combined PnL: `₹{combined:+,.2f}` (Opts: `₹{opt_pnl:+,.2f}`, Futs: `₹{fut_pnl:+,.2f}`)\n"
            f"  - Total Trades: `{num_trades}` | Win Rate: `{win_pct}%`\n"
            f"  - *Strategy Breakdown:*\n"
        )
        
        for strat, s_data in sorted(strat_breakdown.items(), key=lambda x: x[1]['pnl'], reverse=True):
            s_win_pct = round((s_data["wins"] / s_data["trades"]) * 100, 1)
            msg += f"      • {strat:12}: `₹{s_data['pnl']:>+9,.2f}` | {s_data['trades']:>3} trades ({s_win_pct:>4}% WR)\n"
        msg += "\n"

    grand_total = total_opt + total_fut
    grand_win_pct = round((total_wins / total_trades_count) * 100, 1) if total_trades_count > 0 else 0.0

    msg += (
        f"🏆 *Grand Total (Nov 2025 - Feb 2026):*\n"
        f"  - Options PnL: `₹{total_opt:+,.2f}`\n"
        f"  - Futures PnL: `₹{total_fut:+,.2f}`\n"
        f"  - Combined PnL: `₹{grand_total:+,.2f}`\n"
        f"  - Total Trades: `{total_trades_count}` | Win Rate: `{grand_win_pct}%`\n\n"
        f"✅ Continuous multi-month backtest completed successfully!"
    )

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

import asyncio
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

try:
    from tabulate import tabulate
except ImportError:
    def tabulate(rows, headers=None, tablefmt=None):
        if not rows:
            return ""
        all_rows = [headers] + rows if headers else rows
        num_cols = len(all_rows[0])
        col_widths = []
        for col_idx in range(num_cols):
            max_w = 0
            for r in all_rows:
                cell_str = str(r[col_idx]) if col_idx < len(r) else ""
                max_w = max(max_w, len(cell_str))
            col_widths.append(max_w)
            
        output = []
        if headers:
            output.append(" | ".join(str(h).ljust(col_widths[i]) for i, h in enumerate(headers)))
            output.append("-+-".join("-" * w for w in col_widths))
        for r in rows:
            output.append(" | ".join((str(r[i]) if i < len(r) else "").ljust(col_widths[i]) for i in range(num_cols)))
        return "\n".join(output)

IST = ZoneInfo("Asia/Kolkata")

async def run_backtest_with_config(mode, start, end):
    load_config.cache_clear()
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)
    
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    
    # Selective segment disabling via monkey-patching entry points
    if mode == "options_only":
        print("\n⚙️ Mode: Options Only (disabling futures entries)...")
        async def dummy_fut_enter(*args, **kwargs):
            pass
        runtime.futures_trader.maybe_enter = dummy_fut_enter
    elif mode == "futures_only":
        print("\n⚙️ Mode: Futures Only (disabling options entries)...")
        async def dummy_opt_enter(*args, **kwargs):
            pass
        runtime.trader.maybe_enter = dummy_opt_enter
    else:
        print("\n⚙️ Mode: Combined (both options and futures enabled)...")
        
    results = await runtime.run_backtest(start, end, run_regime_agent=True)
    
    if results.get("status") == "error":
        print(f"❌ Backtest failed for mode {mode}: {results.get('reason')}")
        sys.exit(1)
        
    opt_trades = runtime.trader.closed_trades
    fut_trades = [t.to_paper_trade() for t in runtime.futures_trader.closed_trades]
    
    return opt_trades, fut_trades

def get_stats(trades):
    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if t.pnl > 0)
    losing_trades = sum(1 for t in trades if t.pnl < 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    total_pnl = sum(t.pnl for t in trades)
    
    gross_profits = sum(t.pnl for t in trades if t.pnl > 0)
    gross_losses = sum(abs(t.pnl) for t in trades if t.pnl < 0)
    profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else (gross_profits if gross_profits > 0 else 1.0)
    
    return {
        "count": total_trades,
        "wins": winning_trades,
        "losses": losing_trades,
        "win_rate": win_rate,
        "pnl": total_pnl,
        "profit_factor": profit_factor
    }

async def main():
    start = datetime(2026, 6, 1, 9, 0, tzinfo=IST)
    end = datetime(2026, 6, 25, 15, 30, tzinfo=IST)
    
    print("=" * 80)
    print("🔥 SEGMENT COMPARISON BACKTEST (JUNE 1 - JUNE 25, 2026) 🔥")
    print("=" * 80)
    
    # 1. Options Only
    opt_only_opt, opt_only_fut = await run_backtest_with_config("options_only", start, end)
    opt_only_trades = opt_only_opt + opt_only_fut
    
    # 2. Futures Only
    fut_only_opt, fut_only_fut = await run_backtest_with_config("futures_only", start, end)
    fut_only_trades = fut_only_opt + fut_only_fut
    
    # 3. Combined
    comb_opt, comb_fut = await run_backtest_with_config("combined", start, end)
    comb_trades = comb_opt + comb_fut
    
    # Stats
    opt_stats = get_stats(opt_only_trades)
    fut_stats = get_stats(fut_only_trades)
    comb_stats = get_stats(comb_trades)
    
    # Breakdown by day
    dates = []
    current = start.date()
    while current <= end.date():
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
        
    daily_breakdown = {d: {"options_only": 0.0, "futures_only": 0.0, "combined": 0.0} for d in dates}
    
    for t in opt_only_trades:
        d = t.entry_time.astimezone(IST).date()
        if d in daily_breakdown:
            daily_breakdown[d]["options_only"] += t.pnl
            
    for t in fut_only_trades:
        d = t.entry_time.astimezone(IST).date()
        if d in daily_breakdown:
            daily_breakdown[d]["futures_only"] += t.pnl
            
    for t in comb_trades:
        d = t.entry_time.astimezone(IST).date()
        if d in daily_breakdown:
            daily_breakdown[d]["combined"] += t.pnl

    # Print summary
    headers = ["Metric", "Options Only", "Futures Only", "Combined (Both)"]
    rows = [
        ["Total Trades", f"{opt_stats['count']}", f"{fut_stats['count']}", f"{comb_stats['count']}"],
        ["Winning Trades", f"{opt_stats['wins']}", f"{fut_stats['wins']}", f"{comb_stats['wins']}"],
        ["Losing Trades", f"{opt_stats['losses']}", f"{fut_stats['losses']}", f"{comb_stats['losses']}"],
        ["Win Rate", f"{opt_stats['win_rate']:.1f}%", f"{fut_stats['win_rate']:.1f}%", f"{comb_stats['win_rate']:.1f}%"],
        ["Total Net PnL", f"₹{opt_stats['pnl']:+,.2f}", f"₹{fut_stats['pnl']:+,.2f}", f"₹{comb_stats['pnl']:+,.2f}"],
        ["Profit Factor", f"{opt_stats['profit_factor']:.2f}", f"{fut_stats['profit_factor']:.2f}", f"{comb_stats['profit_factor']:.2f}"]
    ]
    
    print("\n" + "=" * 80)
    print("📊 CORE PERFORMANCE COMPARISON")
    print("=" * 80)
    print(tabulate(rows, headers=headers, tablefmt="grid"))
    
    print("\n📅 DAILY NET PNL COMPARISON SUMMARY:")
    breakdown_headers = ["Date", "Options Only PnL", "Futures Only PnL", "Combined PnL"]
    breakdown_rows = []
    for d in sorted(daily_breakdown.keys()):
        breakdown_rows.append([
            d.strftime('%Y-%m-%d (%a)'),
            f"₹{daily_breakdown[d]['options_only']:+,.2f}",
            f"₹{daily_breakdown[d]['futures_only']:+,.2f}",
            f"₹{daily_breakdown[d]['combined']:+,.2f}"
        ])
    print(tabulate(breakdown_rows, headers=breakdown_headers, tablefmt="grid"))
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())

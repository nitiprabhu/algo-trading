import asyncio
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")

async def main():
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)

    # Initialize runtime
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    start = datetime(2026, 6, 16, 9, 0, tzinfo=IST)
    end = datetime(2026, 6, 25, 15, 30, tzinfo=IST)

    print("=" * 60)
    print(f"🚀 STARTING WEEKLY BACKTEST FROM {start.date()} TO {end.date()}")
    print(f"📊 AI Debate Module Status: {'ENABLED' if config.ai.get('debate_enabled') else 'DISABLED'}")
    print(f"🤖 AI System Enabled: {'ENABLED' if config.ai.get('enabled') else 'DISABLED'}")
    print("=" * 60)

    # Run backtest
    results = await runtime.run_backtest(start, end, run_regime_agent=True)

    if results.get("status") == "error":
        print(f"❌ Backtest failed: {results}")
        sys.exit(1)

    closed_trades = runtime.trader.closed_trades + [
        t.to_paper_trade() for t in runtime.futures_trader.closed_trades
    ]

    print("\n" + "="*60)
    print("🏁 BACKTEST COMPLETE")
    print("="*60)

    # Display trades list
    print("\n📜 EXECUTED TRADES LOG:")
    if not closed_trades:
        print("  No trades executed during this period.")
    else:
        headers = ["#", "Instrument", "Type", "Entry Time", "Exit Time", "Entry Px", "Exit Px", "Exit Reason", "PnL (₹)"]
        rows = []
        for idx, t in enumerate(sorted(closed_trades, key=lambda x: x.entry_time), 1):
            entry_str = t.entry_time.astimezone(IST).strftime("%m-%d %H:%M")
            exit_str = t.exit_time.astimezone(IST).strftime("%m-%d %H:%M") if t.exit_time else "Open"
            if "FUT" in t.instrument:
                type_str = f"FUT {t.direction.value}"
            else:
                opt_type = "PE" if "-PE" in t.instrument or "_PE" in t.instrument else "CE"
                type_str = f"OPT {opt_type}"
            rows.append([
                idx,
                t.instrument,
                type_str,
                entry_str,
                exit_str,
                f"{t.entry_price:.2f}",
                f"{t.exit_price:.2f}" if t.exit_price else "N/A",
                t.exit_reason or "N/A",
                f"{t.pnl:+.2f}"
            ])
        print(tabulate(rows, headers=headers, tablefmt="grid"))

    # Group by date for daily breakdown
    daily_pnl = {}
    # Initialize all dates in the range
    current = start.date()
    while current <= end.date():
        # Exclude weekends from breakdown
        if current.weekday() < 5:
            daily_pnl[current] = {"pnl": 0.0, "opt_pnl": 0.0, "fut_pnl": 0.0, "trades": 0}
        current = current + timedelta(days=1)

    for t in closed_trades:
        t_date = t.entry_time.astimezone(IST).date()
        if t_date not in daily_pnl:
            daily_pnl[t_date] = {"pnl": 0.0, "opt_pnl": 0.0, "fut_pnl": 0.0, "trades": 0}
        daily_pnl[t_date]["pnl"] += t.pnl
        if "FUT" in t.instrument:
            daily_pnl[t_date]["fut_pnl"] += t.pnl
        else:
            daily_pnl[t_date]["opt_pnl"] += t.pnl
        daily_pnl[t_date]["trades"] += 1

    print("\n📅 DAILY PERFORMANCE BREAKDOWN:")
    breakdown_rows = []
    total_pnl = 0.0
    total_opt = 0.0
    total_fut = 0.0
    total_trades = 0
    for d, stats in sorted(daily_pnl.items()):
        total_pnl += stats["pnl"]
        total_opt += stats["opt_pnl"]
        total_fut += stats["fut_pnl"]
        total_trades += stats["trades"]
        breakdown_rows.append([
            d.strftime("%Y-%m-%d (%a)"),
            stats["trades"],
            f"₹{stats['opt_pnl']:+.2f}",
            f"₹{stats['fut_pnl']:+.2f}",
            f"₹{stats['pnl']:+.2f}"
        ])
    breakdown_rows.append(["TOTAL", total_trades, f"₹{total_opt:+.2f}", f"₹{total_fut:+.2f}", f"₹{total_pnl:+.2f}"])
    print(tabulate(breakdown_rows, headers=["Date", "Trades Count", "Options PnL", "Futures PnL", "Net PnL"], tablefmt="grid"))

    # Key performance metrics
    print("\n📊 STRATEGY METRICS SUMMARY:")
    wins = [t for t in closed_trades if t.pnl > 0]
    losses = [t for t in closed_trades if t.pnl <= 0]
    win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 0.0

    metrics = [
        ["Total Trades", len(closed_trades)],
        ["Winning Trades", len(wins)],
        ["Losing Trades", len(losses)],
        ["Win Rate", f"{win_rate:.1f}%"],
        ["Total Net PnL", f"₹{total_pnl:+.2f}"]
    ]
    print(tabulate(metrics, headers=["Metric", "Value"], tablefmt="simple"))
    print("=" * 60)

if __name__ == "__main__":
    os.environ["CHARTEDGE_DATA_SOURCE"] = "mock"
    asyncio.run(main())

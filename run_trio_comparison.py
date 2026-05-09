import asyncio
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")

async def run_one_backtest(ai_enabled, debate_enabled, start, end):
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)
    
    # Overwrite config values in memory
    config.ai["enabled"] = ai_enabled
    config.ai["debate_enabled"] = debate_enabled
    
    print(f"\n⚙️ Initializing runtime (AI Enabled: {ai_enabled}, Debate Enabled: {debate_enabled})...")
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    results = await runtime.run_backtest(start, end)
    
    if results.get("status") == "error":
        print(f"❌ Backtest failed: {results.get('reason')}")
        sys.exit(1)
        
    return runtime.trader.closed_trades

def get_stats(trades):
    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if t.pnl > 0)
    losing_trades = sum(1 for t in trades if t.pnl < 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    total_pnl = sum(t.pnl for t in trades)
    
    # Simple profit factor calculation
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
    start = datetime(2026, 5, 1, 9, 0, tzinfo=IST)
    end = datetime(2026, 5, 8, 15, 30, tzinfo=IST)
    
    print("=" * 80)
    print("🔥 RUNNING WEEKLY TRIO COMPARISON BACKTEST (MAY 1 - MAY 8, 2026) 🔥")
    print("=" * 80)
    
    print("\n[1/3] Running Pure Rule-Based Baseline...")
    baseline_trades = await run_one_backtest(ai_enabled=False, debate_enabled=False, start=start, end=end)
    
    print("\n[2/3] Running Rule-Based + Single AI Review...")
    single_ai_trades = await run_one_backtest(ai_enabled=True, debate_enabled=False, start=start, end=end)
    
    print("\n[3/3] Running Rule-Based + AI Debate Enabled...")
    debate_trades = await run_one_backtest(ai_enabled=True, debate_enabled=True, start=start, end=end)
    
    print("\n" + "=" * 80)
    print("📊 WEEKLY TRIO PERFORMANCE ANALYSIS")
    print("=" * 80)
    
    # Calculate stats
    base_stats = get_stats(baseline_trades)
    single_stats = get_stats(single_ai_trades)
    debate_stats = get_stats(debate_trades)
    
    # Organize daily PnLs
    dates = []
    current = start.date()
    while current <= end.date():
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
        
    daily_breakdown = {d: {"base": 0.0, "single": 0.0, "debate": 0.0} for d in dates}
    
    for t in baseline_trades:
        t_date = t.entry_time.astimezone(IST).date()
        if t_date in daily_breakdown:
            daily_breakdown[t_date]["base"] += t.pnl
            
    for t in single_ai_trades:
        t_date = t.entry_time.astimezone(IST).date()
        if t_date in daily_breakdown:
            daily_breakdown[t_date]["single"] += t.pnl
            
    for t in debate_trades:
        t_date = t.entry_time.astimezone(IST).date()
        if t_date in daily_breakdown:
            daily_breakdown[t_date]["debate"] += t.pnl

    headers = ["Metric", "Config A: Pure Rule-Based", "Config B: Single AI Review", "Config C: AI Debate"]
    rows = [
        ["Total Trades", f"{base_stats['count']}", f"{single_stats['count']}", f"{debate_stats['count']}"],
        ["Winning Trades", f"{base_stats['wins']}", f"{single_stats['wins']}", f"{debate_stats['wins']}"],
        ["Losing Trades", f"{base_stats['losses']}", f"{single_stats['losses']}", f"{debate_stats['losses']}"],
        ["Win Rate", f"{base_stats['win_rate']:.1f}%", f"{single_stats['win_rate']:.1f}%", f"{debate_stats['win_rate']:.1f}%"],
        ["Gross Profits", f"₹{sum(t.pnl for t in baseline_trades if t.pnl > 0):+,.2f}", f"₹{sum(t.pnl for t in single_ai_trades if t.pnl > 0):+,.2f}", f"₹{sum(t.pnl for t in debate_trades if t.pnl > 0):+,.2f}"],
        ["Gross Losses", f"₹{sum(t.pnl for t in baseline_trades if t.pnl < 0):+,.2f}", f"₹{sum(t.pnl for t in single_ai_trades if t.pnl < 0):+,.2f}", f"₹{sum(t.pnl for t in debate_trades if t.pnl < 0):+,.2f}"],
        ["Total Net PnL", f"₹{base_stats['pnl']:+,.2f}", f"₹{single_stats['pnl']:+,.2f}", f"₹{debate_stats['pnl']:+,.2f}"],
        ["Profit Factor", f"{base_stats['profit_factor']:.2f}", f"{single_stats['profit_factor']:.2f}", f"{debate_stats['profit_factor']:.2f}"],
    ]
    
    from tabulate import tabulate
    print("\n📈 CORE PERFORMANCE COMPARISON TABLE:")
    print(tabulate(rows, headers=headers, tablefmt="grid"))
    
    print("\n📅 DAILY NET PNL COMPARISON SUMMARY:")
    breakdown_headers = ["Date", "Config A (Rule-Based)", "Config B (Single AI)", "Config C (AI Debate)"]
    breakdown_rows = []
    for d in sorted(daily_breakdown.keys()):
        breakdown_rows.append([
            d.strftime('%Y-%m-%d (%a)'),
            f"₹{daily_breakdown[d]['base']:+,.2f}",
            f"₹{daily_breakdown[d]['single']:+,.2f}",
            f"₹{daily_breakdown[d]['debate']:+,.2f}"
        ])
    print(tabulate(breakdown_rows, headers=breakdown_headers, tablefmt="grid"))
    
    print("\n" + "=" * 80)
    print("🏁 COMPARISON COMPLETE!")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())

"""
6-Month Backtest (Jan-Jun 2026) with Root Cause Analysis
Weekly breakdown + loss week RCA for Intraday + Positional strategies
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
from dotenv import load_dotenv

sys.path.insert(0, "/Users/nithish-prabhu/Downloads/intra-day")
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

load_dotenv()
IST = ZoneInfo("Asia/Kolkata")

async def run_weekly_backtest(start_date, end_date):
    """Run backtest for date range, return trade data"""
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)

    start_dt = datetime.combine(start_date, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
    end_dt = datetime.combine(end_date, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)

    try:
        results = await runtime.run_backtest(start_dt, end_dt, run_regime_agent=True)
        if results.get("status") == "error":
            return None
        return runtime.trader.closed_trades
    except Exception as e:
        print(f"⚠️ Backtest error ({start_date} to {end_date}): {e}")
        return None

def get_week_key(date):
    """Get week identifier: YYYY-W##"""
    week_num = date.isocalendar()[1]
    return f"{date.year}-W{week_num:02d} ({date.strftime('%b %d')})"

async def main():
    print("\n" + "="*100)
    print("📊 6-MONTH BACKTEST WITH RCA (JAN-JUN 2026)")
    print("="*100 + "\n")

    # Run full 6-month backtest
    start = datetime(2026, 1, 1, 9, 0, tzinfo=IST)
    end = datetime(2026, 6, 30, 15, 30, tzinfo=IST)

    print(f"Running {(end.date() - start.date()).days} days of backtest (Jan 1 - Jun 30)...")
    all_trades = await run_weekly_backtest(start.date(), end.date())

    if not all_trades:
        print("❌ Backtest failed")
        return

    # Group trades by week
    weekly_data = defaultdict(lambda: {"trades": [], "pnl": 0.0, "wins": 0, "losses": 0})
    daily_pnl = defaultdict(float)

    for trade in all_trades:
        trade_date = trade.entry_time.astimezone(IST).date()
        week_key = get_week_key(trade_date)

        weekly_data[week_key]["trades"].append(trade)
        weekly_data[week_key]["pnl"] += trade.pnl
        if trade.pnl > 0:
            weekly_data[week_key]["wins"] += 1
        else:
            weekly_data[week_key]["losses"] += 1

        daily_pnl[trade_date] += trade.pnl

    # Weekly summary table
    print("\n" + "="*100)
    print("📅 WEEKLY PERFORMANCE SUMMARY")
    print("="*100)
    print(f"{'Week':<20} | {'Trades':>6} | {'Wins':>4} | {'Win %':>6} | {'PnL (₹)':>12} | {'Status':>8}")
    print("-"*100)

    loss_weeks = []
    for week_key in sorted(weekly_data.keys()):
        week = weekly_data[week_key]
        if week["trades"]:
            win_pct = (week["wins"] / len(week["trades"]) * 100) if week["trades"] else 0
            status = "✅ GAIN" if week["pnl"] > 0 else "⚠️ LOSS"
            print(f"{week_key:<20} | {len(week['trades']):>6} | {week['wins']:>4} | {win_pct:>5.1f}% | ₹{week['pnl']:>11,.0f} | {status:>8}")
            if week["pnl"] < 0:
                loss_weeks.append((week_key, week))

    print("-"*100)
    total_trades = len(all_trades)
    total_pnl = sum(t.pnl for t in all_trades)
    total_wins = sum(1 for t in all_trades if t.pnl > 0)
    total_win_pct = (total_wins / total_trades * 100) if total_trades else 0

    print(f"{'TOTAL':<20} | {total_trades:>6} | {total_wins:>4} | {total_win_pct:>5.1f}% | ₹{total_pnl:>11,.0f} | {'🎯 FINAL':>8}")
    print("="*100 + "\n")

    # RCA on loss weeks
    if loss_weeks:
        print("\n" + "="*100)
        print("🔍 ROOT CAUSE ANALYSIS — LOSS WEEKS")
        print("="*100 + "\n")

        for week_key, week_data in loss_weeks:
            trades = week_data["trades"]
            print(f"\n📌 {week_key} | {len(trades)} trades | ₹{week_data['pnl']:,.0f} loss")
            print("-" * 100)

            # Analyze trades
            winning_trades = [t for t in trades if t.pnl > 0]
            losing_trades = [t for t in trades if t.pnl <= 0]

            gross_wins = sum(t.pnl for t in winning_trades)
            gross_losses = abs(sum(t.pnl for t in losing_trades))

            avg_win = (gross_wins / len(winning_trades)) if winning_trades else 0
            avg_loss = (gross_losses / len(losing_trades)) if losing_trades else 0

            print(f"  Winning trades: {len(winning_trades):>3} | Gross: ₹{gross_wins:>10,.0f} | Avg: ₹{avg_win:>8,.0f}")
            print(f"  Losing trades:  {len(losing_trades):>3} | Gross: ₹{gross_losses:>10,.0f} | Avg: ₹{avg_loss:>8,.0f}")
            print(f"  Profit Factor:  {(gross_wins/gross_losses if gross_losses > 0 else 0):>5.2f}x")

            # Find patterns in losses
            exit_reasons = defaultdict(int)
            for t in losing_trades:
                exit_reasons[t.exit_reason] += 1

            print(f"\n  Exit Reason Distribution (Losses):")
            for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
                pct = (count / len(losing_trades) * 100) if losing_trades else 0
                print(f"    - {reason:<30} {count:>3} trades ({pct:>5.1f}%)")

            # Identify largest losses
            top_3_losses = sorted(losing_trades, key=lambda t: t.pnl)[:3]
            print(f"\n  Top 3 Losses:")
            for i, t in enumerate(top_3_losses, 1):
                print(f"    {i}. {t.entry_time.strftime('%Y-%m-%d %H:%M')} | {t.instrument:<30} | ₹{t.pnl:>8,.0f} | {t.exit_reason}")

            # RCA hypothesis
            print(f"\n  🎯 RCA Hypothesis:")
            if len(losing_trades) > len(winning_trades):
                print(f"     ⚠️ HIGH LOSS FREQUENCY: {len(losing_trades)} losses vs {len(winning_trades)} wins")
                print(f"        → Possible: Poor entry signals, market whipsaw, wrong regime")

            if avg_loss > avg_win * 0.5:
                print(f"     ⚠️ ASYMMETRIC LOSSES: Avg loss ₹{avg_loss:,.0f} vs avg win ₹{avg_win:,.0f}")
                print(f"        → Possible: Stops too wide, failing to cut losses early")

            max_loss_count = sum(1 for t in losing_trades if t.exit_reason == "MAX_LOSS_GUARD")
            if max_loss_count > 0:
                print(f"     ⚠️ MAX LOSS HITS: {max_loss_count} trades hit -10% max loss")
                print(f"        → Possible: Entries in wrong direction, insufficient confluence check")

            eoday_loss = sum(1 for t in losing_trades if "SQUAREOFF" in (t.exit_reason or ""))
            if eoday_loss > 0:
                print(f"     ⚠️ EOD LOSSES: {eoday_loss} trades closed losers at day-end")
                print(f"        → Possible: Overnight gap risk, holding losers into close")

    # Summary stats
    print("\n" + "="*100)
    print("📈 6-MONTH AGGREGATE METRICS")
    print("="*100)
    print(f"Total Trades:           {total_trades}")
    print(f"Winning Trades:         {total_wins} ({total_win_pct:.1f}%)")
    print(f"Losing Trades:          {total_trades - total_wins} ({100-total_win_pct:.1f}%)")
    print(f"Total PnL:              ₹{total_pnl:,.0f}")
    print(f"Profit Factor:          {(sum(t.pnl for t in all_trades if t.pnl>0) / abs(sum(t.pnl for t in all_trades if t.pnl<=0)) if sum(t.pnl for t in all_trades if t.pnl<=0) != 0 else 0):.2f}x")
    print(f"Avg Win / Avg Loss:     ₹{(sum(t.pnl for t in all_trades if t.pnl>0)/total_wins if total_wins else 0):,.0f} / ₹{abs(sum(t.pnl for t in all_trades if t.pnl<=0)/(total_trades-total_wins) if (total_trades-total_wins) else 1):,.0f}")
    print(f"Number of Loss Weeks:   {len(loss_weeks)} out of {len(weekly_data)}")
    print(f"Win Rate (Weeks):       {((len(weekly_data) - len(loss_weeks)) / len(weekly_data) * 100):.1f}%")
    print("="*100 + "\n")

if __name__ == "__main__":
    asyncio.run(main())

"""
June 2026 Positional Trading Backtest — All 3 Strategies

Runs Condor, Straddle, Credit Spread in parallel against real NSE bhavcopy data.
Outputs: weekly breakdown + summary comparison table.
"""
import csv
import glob
import json
import zipfile
import io
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, "/Users/nithish-prabhu/Downloads/intra-day")
from services.chartedge_core.positional_trading import PositionalTradingEngine

PROJECT = "/Users/nithish-prabhu/Downloads/intra-day"

# Load VIX data
vix_by_date = {}
with open(f"{PROJECT}/data/india_vix_1y.json") as f:
    for row in json.load(f):
        vix_by_date[row["date"]] = row["close"]

# Load NSE bhavcopy
chain = defaultdict(lambda: defaultdict(dict))  # date -> expiry -> strike -> {"CE","PE"}
underlying_close = {}
zips = sorted(glob.glob(f"{PROJECT}/data/nse_bhavcopy/BhavCopy_NSE_FO_202606*.zip"))

for zpath in zips:
    with zipfile.ZipFile(zpath) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"))
            for row in reader:
                if row["TckrSymb"] != "NIFTY" or row["OptnTp"] not in ("CE", "PE"):
                    continue
                d, e = row["TradDt"], row["XpryDt"]
                s, c = float(row["StrkPric"]), float(row["ClsPric"])
                chain[d].setdefault(e, {}).setdefault(s, {})[row["OptnTp"]] = c
                underlying_close[d] = float(row["UndrlygPric"])

dates = sorted(chain.keys())

# Run all 3 strategies (in-memory backtest, no DB persistence)
strategies = ["condor", "straddle", "credit_spread"]
engines = {s: PositionalTradingEngine(capital=100000.0, strategy_name=s) for s in strategies}

for s in strategies:
    engines[s].closed_trades = []
    engines[s].open_trade = None

for d in dates:
    today = datetime.strptime(d, "%Y-%m-%d").date()
    spot = underlying_close[d]
    vix = vix_by_date.get(d, 15.0) or 15.0

    for strategy_name, eng in engines.items():
        # Mark-to-market open trade
        if eng.open_trade is not None:
            expiry_chain = chain[d].get(eng.open_trade.expiry, {})
            eng.mark_to_market(today, expiry_chain)

        # Try to enter new trade
        if eng.open_trade is None:
            real_expiries = sorted(e for e in chain[d].keys()
                                  if datetime.strptime(e, "%Y-%m-%d").date() >= today)
            near_term = [e for e in real_expiries
                        if (datetime.strptime(e, "%Y-%m-%d").date() - today).days <= 10]
            if near_term:
                expiry_str = near_term[0]
                expiry_chain = chain[d].get(expiry_str, {})
                expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                if expiry_chain:
                    eng.maybe_enter(today, spot, vix, expiry_chain, target_expiry=expiry_date)

# Compute week groupings for trades
def get_week_label(trade_date):
    """Return week label: Week 1-4 of June"""
    if isinstance(trade_date, str):
        trade_date = datetime.strptime(trade_date, "%Y-%m-%d").date()
    day = trade_date.day
    if day <= 7:
        return "W1 (Jun 1-7)"
    elif day <= 14:
        return "W2 (Jun 8-14)"
    elif day <= 21:
        return "W3 (Jun 15-21)"
    else:
        return "W4 (Jun 22-30)"

# Aggregate stats per strategy
results = {}
for strat_name, eng in engines.items():
    m = eng.metrics() if hasattr(eng, 'metrics') else None
    if not m:
        # Fallback if metrics() not available
        trades = eng.closed_trades
        total = len(trades)
        wins = len([t for t in trades if t.pnl > 0])
        pnl = sum(t.pnl for t in trades)
        m = {"cycles": total, "wins": wins, "win_pct": (wins/total*100 if total else 0), "net_pnl": pnl}

    # Weekly breakdown
    weekly = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for trade in eng.closed_trades:
        week = get_week_label(trade.entry_date)
        weekly[week]["trades"] += 1
        if trade.pnl > 0:
            weekly[week]["wins"] += 1
        weekly[week]["pnl"] += trade.pnl

    results[strat_name] = {
        "summary": m,
        "trades": eng.closed_trades,
        "weekly": weekly,
    }

# Print header
print(f"\n{'='*90}")
print("📊 JUNE 2026 POSITIONAL TRADING BACKTEST — ALL 3 STRATEGIES")
print(f"{'='*90}\n")

# Print aggregated summary
print("SUMMARY TABLE:")
print("-" * 90)
print(f"{'Strategy':<20} | {'Cycles':>6} | {'Wins':>6} | {'Win %':>6} | {'Net PnL (₹)':>15} | {'Avg PnL/Trade':>12}")
print("-" * 90)
for strat_name in ["condor", "straddle", "credit_spread"]:
    s = results[strat_name]["summary"]
    avg_pnl = s["net_pnl"] / s["cycles"] if s["cycles"] > 0 else 0
    print(f"{strat_name:<20} | {s['cycles']:>6} | {s['wins']:>6} | {s['win_pct']:>5.1f}% | ₹{s['net_pnl']:>14,.2f} | ₹{avg_pnl:>11,.2f}")
print("-" * 90 + "\n")

# Weekly breakdown for each strategy
for strat_name in ["condor", "straddle", "credit_spread"]:
    weekly = results[strat_name]["weekly"]
    print(f"\nWEEKLY BREAKDOWN — {strat_name.upper()}:")
    print("-" * 70)
    print(f"{'Week':<15} | {'Trades':>6} | {'Wins':>6} | {'Win %':>6} | {'PnL (₹)':>15}")
    print("-" * 70)
    for week_key in ["W1 (Jun 1-7)", "W2 (Jun 8-14)", "W3 (Jun 15-21)", "W4 (Jun 22-30)"]:
        if week_key in weekly:
            w = weekly[week_key]
            win_pct = (w["wins"] / w["trades"] * 100) if w["trades"] > 0 else 0
            print(f"{week_key:<15} | {w['trades']:>6} | {w['wins']:>6} | {win_pct:>5.1f}% | ₹{w['pnl']:>14,.2f}")
    print("-" * 70)

# Detailed trade list (optional, for debugging)
print("\n\nDETAILED TRADE LIST:")
print("=" * 130)
for strat_name in ["condor", "straddle", "credit_spread"]:
    trades = results[strat_name]["trades"]
    print(f"\n{strat_name.upper()} ({len(trades)} trades):")
    print("-" * 130)
    print(f"{'Entry Date':<12} | {'Expiry':<12} | {'Credit (₹)':>12} | {'Debit (₹)':>12} | {'PnL (₹)':>12} | {'Win/Loss':>8} | {'Exit Reason':<20}")
    print("-" * 130)
    for t in trades:
        status = "WIN" if t.pnl > 0 else "LOSS" if t.pnl < 0 else "BREAKEVEN"
        print(f"{t.entry_date:<12} | {t.expiry:<12} | ₹{t.credit:>11,.2f} | ₹{t.debit if t.debit else 0:>11,.2f} | ₹{t.pnl:>11,.2f} | {status:>8} | {t.exit_reason or 'UNKNOWN':<20}")
    print("-" * 130)

print(f"\n{'='*90}")
print("✅ BACKTEST COMPLETE")
print(f"{'='*90}\n")

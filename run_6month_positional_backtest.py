"""
6-Month Positional Options Backtest (Feb 1 - Jul 20, 2026) — All 3 Strategies
Final validation pass before going live on Upstox weekly positional options.

Uses is_backtesting=True on PositionalTradingEngine -- no DB reads/writes,
fully isolated from the live prod database.
"""
import csv
import glob
import json
import zipfile
import io
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, "/Users/nithish-prabhu/Downloads/intra-day")
from services.chartedge_core.positional_trading import PositionalTradingEngine

PROJECT = "/Users/nithish-prabhu/Downloads/intra-day"
SCRATCH = "/private/tmp/claude-501/-Users-nithish-prabhu-Downloads-intra-day--claude-worktrees-charming-ardinghelli-cec8af/895b9d8f-ac6e-44c9-b461-1de24e3d74fc/scratchpad"

vix_by_date = {}
with open(f"{PROJECT}/data/india_vix_1y.json") as f:
    for row in json.load(f):
        vix_by_date[row["date"]] = row["close"]
with open(f"{SCRATCH}/vix_6mo.json") as f:
    for c in json.load(f)["data"]["candles"]:
        vix_by_date[c[0][:10]] = c[4]

chain = defaultdict(lambda: defaultdict(dict))
underlying_close = {}
zips = sorted(glob.glob(f"{PROJECT}/data/nse_bhavcopy/BhavCopy_NSE_FO_2026*.zip"))
zips = [z for z in zips if "20260201" <= z.split("_")[-1].split(".")[0] <= "20260720"]

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
print(f"Trading days loaded: {len(dates)} ({dates[0]} to {dates[-1]})")

strategies = ["condor", "straddle", "credit_spread"]
engines = {s: PositionalTradingEngine(capital=100000.0, strategy_name=s, is_backtesting=True) for s in strategies}

for d in dates:
    today = datetime.strptime(d, "%Y-%m-%d").date()
    spot = underlying_close[d]
    vix = vix_by_date.get(d, 15.0) or 15.0

    for strategy_name, eng in engines.items():
        if eng.open_trade is not None:
            expiry_chain = chain[d].get(eng.open_trade.expiry, {})
            eng.mark_to_market(today, expiry_chain)

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

def month_key(entry_date_str):
    return entry_date_str[:7]

def week_key(entry_date_str):
    d = datetime.strptime(entry_date_str, "%Y-%m-%d").date()
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"

results = {}
for strat_name, eng in engines.items():
    trades = eng.closed_trades
    total = len(trades)
    wins = len([t for t in trades if t.pnl > 0])
    pnl = sum(t.pnl for t in trades)
    monthly = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    weekly = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        mk = month_key(t.entry_date)
        monthly[mk]["trades"] += 1
        if t.pnl > 0:
            monthly[mk]["wins"] += 1
        monthly[mk]["pnl"] += t.pnl
        wk = week_key(t.entry_date)
        weekly[wk]["trades"] += 1
        if t.pnl > 0:
            weekly[wk]["wins"] += 1
        weekly[wk]["pnl"] += t.pnl
    results[strat_name] = {
        "cycles": total, "wins": wins, "win_pct": (wins / total * 100 if total else 0),
        "net_pnl": pnl, "trades": trades, "monthly": monthly, "weekly": weekly,
        "max_win": max((t.pnl for t in trades), default=0),
        "max_loss": min((t.pnl for t in trades), default=0),
    }

print(f"\n{'='*95}")
print("📊 6-MONTH POSITIONAL BACKTEST (Feb 1 - Jul 20, 2026) — FINAL VALIDATION")
print(f"{'='*95}\n")

print("SUMMARY TABLE:")
print("-" * 95)
print(f"{'Strategy':<16} | {'Cycles':>6} | {'Wins':>6} | {'Win %':>6} | {'Net PnL (₹)':>14} | {'Avg/Trade':>11} | {'Max Win':>10} | {'Max Loss':>10}")
print("-" * 95)
for strat_name in strategies:
    s = results[strat_name]
    avg = s["net_pnl"] / s["cycles"] if s["cycles"] else 0
    print(f"{strat_name:<16} | {s['cycles']:>6} | {s['wins']:>6} | {s['win_pct']:>5.1f}% | ₹{s['net_pnl']:>13,.2f} | ₹{avg:>10,.2f} | ₹{s['max_win']:>9,.2f} | ₹{s['max_loss']:>9,.2f}")
print("-" * 95 + "\n")

for strat_name in strategies:
    print(f"MONTHLY — {strat_name.upper()}:")
    print("-" * 60)
    print(f"{'Month':<10} | {'Trades':>6} | {'Wins':>6} | {'Win %':>6} | {'PnL (₹)':>14}")
    print("-" * 60)
    for mk in sorted(results[strat_name]["monthly"].keys()):
        m = results[strat_name]["monthly"][mk]
        wp = m["wins"] / m["trades"] * 100 if m["trades"] else 0
        print(f"{mk:<10} | {m['trades']:>6} | {m['wins']:>6} | {wp:>5.1f}% | ₹{m['pnl']:>13,.2f}")
    print("-" * 60 + "\n")

print("WEEK-WISE (by entry ISO week):")
print("=" * 90)
all_weeks = sorted(set().union(*(results[s]["weekly"].keys() for s in strategies)))
print(f"{'Week':<10} | {'Condor':>16} | {'Straddle':>16} | {'Credit Spread':>16}")
print("-" * 90)
for wk in all_weeks:
    def cell(s):
        w = results[s]["weekly"].get(wk)
        if not w:
            return "-"
        return f"₹{w['pnl']:>+,.0f} ({w['trades']}t)"
    print(f"{wk:<10} | {cell('condor'):>16} | {cell('straddle'):>16} | {cell('credit_spread'):>16}")
print("=" * 90 + "\n")

print(f"{'='*95}")
print("✅ 6-MONTH BACKTEST COMPLETE — no DB writes (is_backtesting=True)")
print(f"{'='*95}\n")

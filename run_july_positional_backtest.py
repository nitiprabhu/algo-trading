"""
July 2026 Positional Trading Backtest — All 3 Strategies (Jul 1 - Jul 20)

Same as run_june_positional_backtest.py but for July, with VIX pulled fresh
from Upstox historical-candle API (local india_vix_1y.json only covered
through Jul 6).
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

# VIX: local file (through Jul 6) + fresh Upstox fetch (Jul 7-20) merged
vix_by_date = {}
with open(f"{PROJECT}/data/india_vix_1y.json") as f:
    for row in json.load(f):
        vix_by_date[row["date"]] = row["close"]
with open("/private/tmp/claude-501/-Users-nithish-prabhu-Downloads-intra-day--claude-worktrees-charming-ardinghelli-cec8af/895b9d8f-ac6e-44c9-b461-1de24e3d74fc/scratchpad/vix_jul.json") as f:
    for c in json.load(f)["data"]["candles"]:
        vix_by_date[c[0][:10]] = c[4]

# Load NSE bhavcopy for July 1-20
chain = defaultdict(lambda: defaultdict(dict))
underlying_close = {}
zips = sorted(glob.glob(f"{PROJECT}/data/nse_bhavcopy/BhavCopy_NSE_FO_202607*.zip"))
zips = [z for z in zips if int(z.split("_")[-1].split(".")[0][6:8]) <= 20]

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
print(f"Trading days loaded: {dates}")

strategies = ["condor", "straddle", "credit_spread"]
engines = {s: PositionalTradingEngine(capital=100000.0, strategy_name=s, is_backtesting=True) for s in strategies}
for s in strategies:
    engines[s].closed_trades = []
    engines[s].open_trade = None

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

results = {}
for strat_name, eng in engines.items():
    m = eng.metrics() if hasattr(eng, "metrics") else None
    if not m:
        trades = eng.closed_trades
        total = len(trades)
        wins = len([t for t in trades if t.pnl > 0])
        pnl = sum(t.pnl for t in trades)
        m = {"cycles": total, "wins": wins, "win_pct": (wins / total * 100 if total else 0), "net_pnl": pnl}
    results[strat_name] = {"summary": m, "trades": eng.closed_trades}

print(f"\n{'='*90}")
print("📊 JULY 2026 POSITIONAL TRADING BACKTEST (Jul 1-20) — ALL 3 STRATEGIES")
print(f"{'='*90}\n")

print("SUMMARY TABLE:")
print("-" * 90)
print(f"{'Strategy':<20} | {'Cycles':>6} | {'Wins':>6} | {'Win %':>6} | {'Net PnL (₹)':>15} | {'Avg PnL/Trade':>12}")
print("-" * 90)
for strat_name in strategies:
    s = results[strat_name]["summary"]
    avg_pnl = s["net_pnl"] / s["cycles"] if s["cycles"] > 0 else 0
    print(f"{strat_name:<20} | {s['cycles']:>6} | {s['wins']:>6} | {s['win_pct']:>5.1f}% | ₹{s['net_pnl']:>14,.2f} | ₹{avg_pnl:>11,.2f}")
print("-" * 90 + "\n")

print("DETAILED TRADE LIST:")
print("=" * 130)
for strat_name in strategies:
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

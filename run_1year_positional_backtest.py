"""
1-Year Positional Options Backtest (Jul 7, 2025 - Jul 20, 2026) — All 3 Strategies
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

START, END = "2025-07-07", "2026-07-20"

vix_by_date = {}
with open(f"{PROJECT}/data/india_vix_1y.json") as f:
    for row in json.load(f):
        vix_by_date[row["date"]] = row["close"]
with open(f"{SCRATCH}/vix_6mo.json") as f:
    for c in json.load(f)["data"]["candles"]:
        vix_by_date[c[0][:10]] = c[4]
with open(f"{SCRATCH}/vix_jul.json") as f:
    for c in json.load(f)["data"]["candles"]:
        vix_by_date[c[0][:10]] = c[4]

chain = defaultdict(lambda: defaultdict(dict))
underlying_close = {}
all_zips = sorted(glob.glob(f"{PROJECT}/data/nse_bhavcopy/BhavCopy_NSE_FO_2025*.zip") +
                   glob.glob(f"{PROJECT}/data/nse_bhavcopy/BhavCopy_NSE_FO_2026*.zip"))
zips = []
for z in all_zips:
    tag = z.split("_")[-1].split(".")[0]
    if len(tag) == 8 and START.replace("-", "") <= tag <= END.replace("-", ""):
        zips.append(z)

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

strategies = ["condor", "straddle", "credit_spread", "iv_gated_straddle"]
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

results = {}
for strat_name, eng in engines.items():
    trades = eng.closed_trades
    total = len(trades)
    wins = len([t for t in trades if t.pnl > 0])
    pnl = sum(t.pnl for t in trades)
    monthly = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        mk = month_key(t.entry_date)
        monthly[mk]["trades"] += 1
        if t.pnl > 0:
            monthly[mk]["wins"] += 1
        monthly[mk]["pnl"] += t.pnl
    pnls = [t.pnl for t in trades]
    losses = [p for p in pnls if p < 0]
    wins_list = [p for p in pnls if p > 0]
    # max drawdown on cumulative equity curve (chronological)
    trades_sorted = sorted(trades, key=lambda t: t.entry_date)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades_sorted:
        equity += t.pnl
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    results[strat_name] = {
        "cycles": total, "wins": wins, "win_pct": (wins / total * 100 if total else 0),
        "net_pnl": pnl, "trades": trades, "monthly": monthly,
        "max_win": max(pnls, default=0), "max_loss": min(pnls, default=0),
        "avg_win": sum(wins_list) / len(wins_list) if wins_list else 0,
        "avg_loss": sum(losses) / len(losses) if losses else 0,
        "max_drawdown": max_dd,
    }

print(f"\n{'='*100}")
print("📊 1-YEAR POSITIONAL BACKTEST (Jul 7, 2025 - Jul 20, 2026) — FINAL VALIDATION")
print(f"{'='*100}\n")

print("SUMMARY TABLE:")
print("-" * 100)
print(f"{'Strategy':<14} | {'Cyc':>4} | {'Win%':>5} | {'Net PnL':>13} | {'Avg Win':>10} | {'Avg Loss':>10} | {'Max Win':>10} | {'Max Loss':>10} | {'MaxDD':>10}")
print("-" * 100)
for strat_name in strategies:
    s = results[strat_name]
    print(f"{strat_name:<14} | {s['cycles']:>4} | {s['win_pct']:>4.1f}% | ₹{s['net_pnl']:>12,.0f} | ₹{s['avg_win']:>9,.0f} | ₹{s['avg_loss']:>9,.0f} | ₹{s['max_win']:>9,.0f} | ₹{s['max_loss']:>9,.0f} | ₹{s['max_drawdown']:>9,.0f}")
print("-" * 100 + "\n")

for strat_name in strategies:
    print(f"MONTHLY — {strat_name.upper()}:")
    print("-" * 55)
    print(f"{'Month':<10} | {'Trades':>6} | {'Win %':>6} | {'PnL (₹)':>13}")
    print("-" * 55)
    for mk in sorted(results[strat_name]["monthly"].keys()):
        m = results[strat_name]["monthly"][mk]
        wp = m["wins"] / m["trades"] * 100 if m["trades"] else 0
        print(f"{mk:<10} | {m['trades']:>6} | {wp:>5.1f}% | ₹{m['pnl']:>12,.2f}")
    print("-" * 55 + "\n")

print(f"{'='*100}")
print("✅ 1-YEAR BACKTEST COMPLETE — no DB writes (is_backtesting=True)")
print(f"{'='*100}\n")

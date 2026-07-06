"""
Real-data 2-year multi-strategy options comparison.
Data: NSE F&O bhavcopy (real settlement prices) + real India VIX (last 12mo) / VIX=15 fallback (Jul24-Jun25).
Same exit rules across all strategies for fair comparison: 55% profit-take, 2.2x credit stop, else hold to expiry.
Strategies tested:
  A) NIFTY weekly Iron Condor      (sigma-scaled short 0.85 / wing 1.30, defined risk)
  B) NIFTY weekly Short Straddle   (ATM CE+PE, no wings -- undefined risk, higher premium)
  C) NIFTY weekly Credit Spread    (trend-aligned single side: sell put-spread if 5d up, call-spread if down)
  D) BANKNIFTY monthly Iron Condor (same sigma method, monthly expiry cycle)
"""
import csv, glob, json, math, zipfile, io
from collections import defaultdict
from datetime import datetime

PROJECT = "/Users/nithish-prabhu/Downloads/intra-day"
STEP = 50
LOT_NIFTY = 75
LOT_BANKNIFTY = 30
PROFIT_TAKE_FRAC = 0.55
STOP_MULT = 2.2

vix_by_date = {}
with open(f"{PROJECT}/data/india_vix_1y.json") as f:
    for row in json.load(f):
        vix_by_date[row["date"]] = row["close"]

chain = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))  # date->sym->expiry->strike->{"CE":c,"PE":c}
underlying_close = defaultdict(dict)

zips = sorted(glob.glob(f"{PROJECT}/data/nse_bhavcopy/*.zip"))
for zpath in zips:
    with zipfile.ZipFile(zpath) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"))
            for row in reader:
                sym = row["TckrSymb"]
                if sym not in ("NIFTY", "BANKNIFTY"):
                    continue
                optn = row["OptnTp"]
                if optn not in ("CE", "PE"):
                    continue
                date = row["TradDt"]
                expiry = row["XpryDt"]
                strike = float(row["StrkPric"])
                close = float(row["ClsPric"])
                chain[date][sym][expiry].setdefault(strike, {})[optn] = close
                underlying_close[date][sym] = float(row["UndrlygPric"])

dates = sorted(chain.keys())


def pick_strike(strikes, target):
    return min(strikes, key=lambda s: abs(s - target))


def get_expiries(sym, kind):
    """kind: 'weekly' picks all distinct expiries seen; 'monthly' picks last expiry of each month."""
    all_exp = sorted({e for d in dates for e in chain[d].get(sym, {}).keys()})
    if kind == "weekly":
        return all_exp
    # monthly: last expiry falling in each calendar month
    by_month = defaultdict(list)
    for e in all_exp:
        by_month[e[:7]].append(e)
    return sorted(v[-1] for v in by_month.values())


def daily_trend_pct(sym, entry_date, lookback=5):
    prior_dates = [d for d in dates if d < entry_date and sym in underlying_close.get(d, {})]
    if len(prior_dates) < lookback + 1:
        return 0.0
    old = underlying_close[prior_dates[-lookback - 1]][sym]
    new = underlying_close[prior_dates[-1]][sym]
    return (new - old) / old * 100.0 if old else 0.0


def run_condor(sym, lot, expiries):
    trades = []
    for expiry in expiries:
        prior = [e for e in expiries if e < expiry]
        candidates = ([d for d in dates if d > prior[-1] and expiry in chain[d].get(sym, {})]
                      if prior else [d for d in dates if d <= expiry and expiry in chain[d].get(sym, {})])
        if not candidates:
            continue
        entry_date = candidates[0]
        entry_chain = chain[entry_date][sym][expiry]
        spot = underlying_close[entry_date][sym]
        vix = vix_by_date.get(entry_date, 15.0) or 15.0
        dte = max((datetime.strptime(expiry, "%Y-%m-%d") - datetime.strptime(entry_date, "%Y-%m-%d")).days, 1)
        sigma = spot * (vix / 100.0) * math.sqrt(dte / 365.0)
        short_off = max(round(sigma * 0.85 / STEP) * STEP, 2 * STEP)
        wing_off = max(round(sigma * 1.30 / STEP) * STEP, short_off + 2 * STEP)
        atm = round(spot / STEP) * STEP
        avail = list(entry_chain.keys())
        short_pe, long_pe = pick_strike(avail, atm - short_off), pick_strike(avail, atm - wing_off)
        short_ce, long_ce = pick_strike(avail, atm + short_off), pick_strike(avail, atm + wing_off)
        try:
            credit = (entry_chain[short_pe]["PE"] + entry_chain[short_ce]["CE"]) - \
                     (entry_chain[long_pe]["PE"] + entry_chain[long_ce]["CE"])
        except KeyError:
            continue
        if credit <= 0:
            continue
        pnl, exit_date = _walk_exit(sym, expiry, entry_date,
                                     lambda dc: (dc[short_pe]["PE"] + dc[short_ce]["CE"]) - (dc[long_pe]["PE"] + dc[long_ce]["CE"]),
                                     credit, lot)
        if pnl is not None:
            trades.append({"expiry": expiry, "entry_date": entry_date, "pnl": pnl})
    return trades


def run_straddle(sym, lot, expiries):
    trades = []
    for expiry in expiries:
        prior = [e for e in expiries if e < expiry]
        candidates = ([d for d in dates if d > prior[-1] and expiry in chain[d].get(sym, {})]
                      if prior else [d for d in dates if d <= expiry and expiry in chain[d].get(sym, {})])
        if not candidates:
            continue
        entry_date = candidates[0]
        entry_chain = chain[entry_date][sym][expiry]
        spot = underlying_close[entry_date][sym]
        atm = pick_strike(list(entry_chain.keys()), round(spot / STEP) * STEP)
        try:
            credit = entry_chain[atm]["CE"] + entry_chain[atm]["PE"]
        except KeyError:
            continue
        if credit <= 0:
            continue
        pnl, exit_date = _walk_exit(sym, expiry, entry_date,
                                     lambda dc: dc[atm]["CE"] + dc[atm]["PE"],
                                     credit, lot)
        if pnl is not None:
            trades.append({"expiry": expiry, "entry_date": entry_date, "pnl": pnl})
    return trades


def run_credit_spread(sym, lot, expiries):
    trades = []
    for expiry in expiries:
        prior = [e for e in expiries if e < expiry]
        candidates = ([d for d in dates if d > prior[-1] and expiry in chain[d].get(sym, {})]
                      if prior else [d for d in dates if d <= expiry and expiry in chain[d].get(sym, {})])
        if not candidates:
            continue
        entry_date = candidates[0]
        entry_chain = chain[entry_date][sym][expiry]
        spot = underlying_close[entry_date][sym]
        vix = vix_by_date.get(entry_date, 15.0) or 15.0
        dte = max((datetime.strptime(expiry, "%Y-%m-%d") - datetime.strptime(entry_date, "%Y-%m-%d")).days, 1)
        sigma = spot * (vix / 100.0) * math.sqrt(dte / 365.0)
        short_off = max(round(sigma * 0.85 / STEP) * STEP, 2 * STEP)
        wing_off = max(round(sigma * 1.30 / STEP) * STEP, short_off + 2 * STEP)
        atm = round(spot / STEP) * STEP
        avail = list(entry_chain.keys())
        trend = daily_trend_pct(sym, entry_date)
        bullish = trend >= 0  # sell put-spread if trending up (bullish), else call-spread
        try:
            if bullish:
                short_k, long_k = pick_strike(avail, atm - short_off), pick_strike(avail, atm - wing_off)
                credit = entry_chain[short_k]["PE"] - entry_chain[long_k]["PE"]
                mtm_fn = lambda dc, sk=short_k, lk=long_k: dc[sk]["PE"] - dc[lk]["PE"]
            else:
                short_k, long_k = pick_strike(avail, atm + short_off), pick_strike(avail, atm + wing_off)
                credit = entry_chain[short_k]["CE"] - entry_chain[long_k]["CE"]
                mtm_fn = lambda dc, sk=short_k, lk=long_k: dc[sk]["CE"] - dc[lk]["CE"]
        except KeyError:
            continue
        if credit <= 0:
            continue
        pnl, exit_date = _walk_exit(sym, expiry, entry_date, mtm_fn, credit, lot)
        if pnl is not None:
            trades.append({"expiry": expiry, "entry_date": entry_date, "pnl": pnl})
    return trades


def _walk_exit(sym, expiry, entry_date, mtm_fn, credit, lot):
    cycle_dates = [d for d in dates if entry_date < d <= expiry and expiry in chain[d].get(sym, {})]
    for d in cycle_dates:
        try:
            cur_debit = mtm_fn(chain[d][sym][expiry])
        except KeyError:
            continue
        if cur_debit <= credit * (1 - PROFIT_TAKE_FRAC) or cur_debit >= credit * STOP_MULT:
            return round((credit - cur_debit) * lot, 2), d
    exit_candidates = [d for d in dates if d <= expiry and expiry in chain[d].get(sym, {})]
    if not exit_candidates:
        return None, None
    exit_date = exit_candidates[-1]
    try:
        debit = mtm_fn(chain[exit_date][sym][expiry])
    except KeyError:
        return None, None
    return round((credit - debit) * lot, 2), exit_date


import sys

LAST_WEEK_ONLY = "--last-week" in sys.argv

nifty_weekly = get_expiries("NIFTY", "weekly")
banknifty_monthly = get_expiries("BANKNIFTY", "monthly")

results = {
    "A) NIFTY Weekly Condor": run_condor("NIFTY", LOT_NIFTY, nifty_weekly),
    "B) NIFTY Weekly Straddle": run_straddle("NIFTY", LOT_NIFTY, nifty_weekly),
    "C) NIFTY Weekly Credit Spread": run_credit_spread("NIFTY", LOT_NIFTY, nifty_weekly),
    "D) BANKNIFTY Monthly Condor": run_condor("BANKNIFTY", LOT_BANKNIFTY, banknifty_monthly),
}

if LAST_WEEK_ONLY:
    # Keep only each strategy's most recent completed cycle (last entry_date).
    print(f"\n{'='*90}\nLAST COMPLETED CYCLE PER STRATEGY — REAL DATA (bhavcopy up to {dates[-1]})\n{'='*90}")
    print(f"{'Strategy':32s} {'Entry':11s} {'Expiry':11s} {'PnL':>12s}")
    for name in results:
        sorted_t = sorted(results[name], key=lambda t: t["entry_date"])
        # last COMPLETED cycle: expiry must be <= last available data date
        completed = [t for t in sorted_t if t["expiry"] <= dates[-1]]
        results[name] = completed[-1:] if completed else sorted_t[-1:]
    for name, trades in results.items():
        for t in trades:
            print(f"{name:32s} {t['entry_date']:11s} {t['expiry']:11s} {t['pnl']:12,.2f}")
    print(f"{'='*90}\n")
    sys.exit(0)

print(f"\n{'='*90}\nSTRATEGY COMPARISON — REAL DATA, Jul 2024 to Jul 2026 (2 years)\n{'='*90}")
print(f"{'Strategy':32s} {'Cycles':>7s} {'Wins':>6s} {'Win%':>6s} {'Net PnL':>14s}")
summary_lines = []
for name, trades in results.items():
    n = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    win_pct = wins / n * 100 if n else 0
    total = sum(t["pnl"] for t in trades)
    print(f"{name:32s} {n:7d} {wins:6d} {win_pct:5.0f}% {total:14,.2f}")
    summary_lines.append((name, n, wins, win_pct, total))
print(f"{'='*90}\n")

# monthly breakdown per strategy
report_path = f"{PROJECT}/reports/real_data_options_strategy_comparison_2024-07_to_2026-07.md"
with open(report_path, "w") as f:
    f.write("# Real-Data Options Strategy Comparison — Jul 2024 to Jul 2026 (2 years)\n\n")
    f.write("**Data:** NSE F&O bhavcopy real settlement prices + real India VIX (last 12mo; VIX=15 fallback Jul24-Jun25). ")
    f.write(f"**Exit rules (all strategies):** {int(PROFIT_TAKE_FRAC*100)}% profit-take, {STOP_MULT}x credit stop, else hold to expiry.\n\n")
    f.write("## Summary\n\n| Strategy | Cycles | Wins | Win% | Net PnL |\n|---|---|---|---|---|\n")
    for name, n, wins, win_pct, total in summary_lines:
        f.write(f"| {name} | {n} | {wins} | {win_pct:.0f}% | Rs {total:,.2f} |\n")
    f.write("\n")
    for name, trades in results.items():
        f.write(f"\n## {name} — Monthly Breakdown\n\n| Month | Cycles | Wins | Win% | Net PnL |\n|---|---|---|---|---|\n")
        monthly = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})
        for t in trades:
            m = t["entry_date"][:7]
            monthly[m]["pnl"] += t["pnl"]; monthly[m]["trades"] += 1
            if t["pnl"] > 0:
                monthly[m]["wins"] += 1
        for m in sorted(monthly.keys()):
            mm = monthly[m]
            wp = mm["wins"] / mm["trades"] * 100 if mm["trades"] else 0
            f.write(f"| {m} | {mm['trades']} | {mm['wins']} | {wp:.0f}% | Rs {mm['pnl']:,.2f} |\n")

print(f"Report saved to: {report_path}")

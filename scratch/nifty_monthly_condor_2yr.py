"""
NIFTY monthly (last Tuesday) Iron Condor backtest on 2yr real NSE bhavcopy data.
Compare to weekly: validate whether monthly expiry has edge too.
"""
import csv, glob, json, math, zipfile, io
from collections import defaultdict
from datetime import datetime, timedelta

PROJECT = "/Users/nithish-prabhu/Downloads/intra-day"
STEP = 50
LOT_NIFTY = 75
PROFIT_TAKE_FRAC = 0.55
STOP_MULT = 1.1

vix_by_date = {}
with open(f"{PROJECT}/data/india_vix_1y.json") as f:
    for row in json.load(f):
        vix_by_date[row["date"]] = row["close"]

chain = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
underlying_close = defaultdict(dict)

zips = sorted(glob.glob(f"{PROJECT}/data/nse_bhavcopy/*.zip"))
for zpath in zips:
    with zipfile.ZipFile(zpath) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"))
            for row in reader:
                sym = row["TckrSymb"]
                if sym not in ("NIFTY",):
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

def last_tuesday_of_month(d: datetime) -> datetime:
    """Last Tuesday of the month for datetime d."""
    y, m = d.year, d.month
    if m == 12:
        next_month = datetime(y + 1, 1, 1)
    else:
        next_month = datetime(y, m + 1, 1)
    last_day = next_month - timedelta(days=1)
    days_back = (last_day.weekday() - 1) % 7
    return last_day - timedelta(days=days_back)

def is_last_tuesday(d: datetime) -> bool:
    return d.date() == last_tuesday_of_month(d).date()

def pick_strike(strikes, target):
    return min(strikes, key=lambda s: abs(s - target))

# Get monthly expiries (last Tuesday of each month seen in data)
all_exp = sorted({e for d in dates for e in chain[d].get("NIFTY", {}).keys()})
monthly_expiries = []
for e in all_exp:
    ed = datetime.strptime(e, "%Y-%m-%d")
    if is_last_tuesday(ed):
        monthly_expiries.append(e)

print(f"Found {len(monthly_expiries)} monthly expiries in 2yr: {monthly_expiries[:5]}...")

trades = []
for expiry in monthly_expiries:
    prior = [e for e in monthly_expiries if e < expiry]
    candidates = ([d for d in dates if d > prior[-1] and expiry in chain[d].get("NIFTY", {})]
                  if prior else [d for d in dates if d <= expiry and expiry in chain[d].get("NIFTY", {})])
    if not candidates:
        continue
    entry_date = candidates[0]
    entry_chain = chain[entry_date]["NIFTY"][expiry]
    spot = underlying_close[entry_date]["NIFTY"]
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

    pnl, exit_date = None, None
    cycle_dates = [d for d in dates if entry_date < d <= expiry and expiry in chain[d].get("NIFTY", {})]
    for d in cycle_dates:
        try:
            debit = (chain[d]["NIFTY"][expiry][short_pe]["PE"] + chain[d]["NIFTY"][expiry][short_ce]["CE"]) - \
                    (chain[d]["NIFTY"][expiry][long_pe]["PE"] + chain[d]["NIFTY"][expiry][long_ce]["CE"])
        except KeyError:
            continue
        if debit <= credit * (1 - PROFIT_TAKE_FRAC) or debit >= credit * STOP_MULT:
            pnl = round((credit - debit) * LOT_NIFTY, 2)
            exit_date = d
            break
    if pnl is None:
        exit_candidates = [d for d in dates if d <= expiry and expiry in chain[d].get("NIFTY", {})]
        if exit_candidates:
            exit_date = exit_candidates[-1]
            try:
                debit = (chain[exit_date]["NIFTY"][expiry][short_pe]["PE"] + chain[exit_date]["NIFTY"][expiry][short_ce]["CE"]) - \
                        (chain[exit_date]["NIFTY"][expiry][long_pe]["PE"] + chain[exit_date]["NIFTY"][expiry][long_ce]["CE"])
                pnl = round((credit - debit) * LOT_NIFTY, 2)
            except KeyError:
                continue
    if pnl is not None:
        trades.append({"expiry": expiry, "entry_date": entry_date, "exit_date": exit_date, "pnl": pnl})

print(f"\n{'='*80}")
print(f"NIFTY MONTHLY IRON CONDOR — 2YR (Jul 2024–Jul 2026)")
print(f"{'='*80}")
n = len(trades)
wins = sum(1 for t in trades if t["pnl"] > 0)
total = sum(t["pnl"] for t in trades)
win_pct = wins / n * 100 if n else 0
print(f"Cycles: {n} | Wins: {wins} | Win%: {win_pct:.0f}% | Net PnL: Rs {total:,.2f}")
print(f"\nComparison to WEEKLY (106 cycles, 75%, +71,677):")
print(f"  Monthly avg/cycle: {total/n if n else 0:,.0f}")
print(f"  Weekly avg/cycle: {71677/106:,.0f}")

monthly = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})
for t in trades:
    m = t["entry_date"][:7]
    monthly[m]["pnl"] += t["pnl"]; monthly[m]["trades"] += 1
    if t["pnl"] > 0: monthly[m]["wins"] += 1

print(f"\n{'Month':10s} {'Cycles':>7s} {'Wins':>6s} {'Win%':>6s} {'PnL':>12s}")
for m in sorted(monthly.keys()):
    mm = monthly[m]
    wp = mm["wins"] / mm["trades"] * 100 if mm["trades"] else 0
    print(f"{m:10s} {mm['trades']:7d} {mm['wins']:6d} {wp:5.0f}% {mm['pnl']:12,.2f}")

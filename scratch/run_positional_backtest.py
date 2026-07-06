"""
Runs the REAL positional_trading.py module (not a scratch reimplementation) against
2 years of real NSE bhavcopy data, to confirm the actual shipped module reproduces
the validated result (+Rs 71,677, 106 cycles, 75% win, Jul 2024 - Jul 2026).
"""
import csv, glob, json, zipfile, io, sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, "/Users/nithish-prabhu/Downloads/intra-day")
from services.chartedge_core.positional_trading import PositionalTradingEngine

PROJECT = "/Users/nithish-prabhu/Downloads/intra-day"

vix_by_date = {}
with open(f"{PROJECT}/data/india_vix_1y.json") as f:
    for row in json.load(f):
        vix_by_date[row["date"]] = row["close"]

chain = defaultdict(lambda: defaultdict(dict))   # date -> expiry -> strike -> {"CE","PE"}
underlying_close = {}
zips = sorted(glob.glob(f"{PROJECT}/data/nse_bhavcopy/*.zip"))

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

eng = PositionalTradingEngine(capital=100000.0, log_path="/tmp/positional_module_test.json")
eng.closed_trades = []
eng.open_trade = None

for d in dates:
    today = datetime.strptime(d, "%Y-%m-%d").date()
    spot = underlying_close[d]
    vix = vix_by_date.get(d, 15.0) or 15.0

    if eng.open_trade is not None:
        expiry_chain = chain[d].get(eng.open_trade.expiry, {})
        # convert strike-keyed dict for mark_to_market lookup
        eng.mark_to_market(today, expiry_chain)

    if eng.open_trade is None:
        # Resolve the REAL next expiry from that day's actual chain (holidays shift
        # NSE expiries) rather than trusting pure weekday arithmetic.
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

m = eng.metrics()
print(f"\n{'='*70}")
print("REAL MODULE TEST (positional_trading.py) vs bhavcopy, Jul 2024 - Jul 2026")
print(f"{'='*70}")
print(f"Cycles: {m['cycles']} | Wins: {m['wins']} ({m['win_pct']}%) | Net PnL: Rs {m['net_pnl']:,.2f}")
print(f"{'='*70}\n")

import os
if os.path.exists("/tmp/positional_module_test.json"):
    os.remove("/tmp/positional_module_test.json")

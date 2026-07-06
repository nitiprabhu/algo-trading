"""
Real-data 2-year NIFTY daily gap-fill mean-reversion futures backtest.
Idea: big overnight gap (open vs prior close) tends to partially fill intraday.
Fade the gap at open, target = prior close (full fill) or partial, stop = gap extension.
Data: NSE F&O bhavcopy front-month futures (real OHLC), no intraday candles.
"""
import csv, glob, zipfile, io
from collections import defaultdict

PROJECT = "/Users/nithish-prabhu/Downloads/intra-day"
LOT = 75
TOTAL_CAPITAL = 500000.0
RISK_PCT = 0.75
MIN_GAP_PCT = 0.35   # only fade gaps at least this big
TARGET_FILL_FRAC = 0.5  # target 50% gap fill
STOP_EXTRA_FRAC = 0.5   # stop at gap + 50% more gap distance

bars = {}
zips = sorted(glob.glob(f"{PROJECT}/data/nse_bhavcopy/*.zip"))
for zpath in zips:
    with zipfile.ZipFile(zpath) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"))
            candidates = []
            date = None
            for row in reader:
                if row["TckrSymb"] != "NIFTY" or row["FinInstrmTp"] != "IDF":
                    continue
                date = row["TradDt"]
                candidates.append((row["XpryDt"], row))
            if candidates:
                candidates.sort(key=lambda x: x[0])
                front = candidates[0][1]
                bars[date] = {
                    "open": float(front["OpnPric"]),
                    "high": float(front["HghPric"]),
                    "low": float(front["LwPric"]),
                    "close": float(front["ClsPric"]),
                }

dates = sorted(bars.keys())
trades = []

for i in range(1, len(dates)):
    d = dates[i]
    prev_close = bars[dates[i - 1]]["close"]
    o, h, l, c = bars[d]["open"], bars[d]["high"], bars[d]["low"], bars[d]["close"]
    gap_pct = (o - prev_close) / prev_close * 100

    if abs(gap_pct) < MIN_GAP_PCT:
        continue

    gap_dist = abs(o - prev_close)
    target_fill = TARGET_FILL_FRAC * gap_dist
    stop_dist = gap_dist * (1 + STOP_EXTRA_FRAC)

    risk_budget = TOTAL_CAPITAL * (RISK_PCT / 100.0)
    lots = max(1, int(risk_budget // (stop_dist * LOT)))
    qty = LOT * lots

    if gap_pct > 0:
        # gap up -> fade (SELL), target = open - target_fill, stop = open + stop_dist - gap_dist
        direction = "SELL"
        entry = o
        target = o - target_fill
        sl = o + (stop_dist - gap_dist)
        hit_sl = h >= sl
        hit_target = l <= target
    else:
        direction = "BUY"
        entry = o
        target = o + target_fill
        sl = o - (stop_dist - gap_dist)
        hit_sl = l <= sl
        hit_target = h >= target

    if hit_sl and hit_target:
        # ambiguous same-day - conservative: assume SL hit first (real risk)
        exit_price, reason = sl, "SL"
    elif hit_sl:
        exit_price, reason = sl, "SL"
    elif hit_target:
        exit_price, reason = target, "TARGET"
    else:
        exit_price, reason = c, "EOD"

    direction_mult = 1 if direction == "BUY" else -1
    pnl = (exit_price - entry) * direction_mult * qty
    trades.append({
        "date": d, "dir": direction, "gap_pct": round(gap_pct, 2),
        "entry": entry, "exit": exit_price, "reason": reason, "qty": qty, "pnl": round(pnl, 2),
    })

print(f"\n{'='*100}")
print(f"REAL-DATA 2-YEAR NIFTY GAP-FILL FUTURES BACKTEST (min_gap={MIN_GAP_PCT}%, target_fill={TARGET_FILL_FRAC})")
print(f"{'='*100}")
print(f"{'Date':11s} {'Dir':4s} {'Gap%':>6s} {'Entry':>9s} {'Exit':>9s} {'Reason':8s} {'Qty':>5s} {'PnL':>10s}")
for t in trades:
    print(f"{t['date']:11s} {t['dir']:4s} {t['gap_pct']:6.2f} {t['entry']:9.1f} {t['exit']:9.1f} "
          f"{t['reason']:8s} {t['qty']:5d} {t['pnl']:10.2f}")

monthly = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})
for t in trades:
    month = t["date"][:7]
    monthly[month]["pnl"] += t["pnl"]
    monthly[month]["trades"] += 1
    if t["pnl"] > 0:
        monthly[month]["wins"] += 1

print(f"\n{'='*70}\nMONTH-WISE P/L SUMMARY\n{'='*70}")
print(f"{'Month':10s} {'Trades':>7s} {'Wins':>6s} {'Win%':>6s} {'Net PnL':>12s}")
total_pnl, total_trades, total_wins = 0.0, 0, 0
for month in sorted(monthly.keys()):
    m = monthly[month]
    win_pct = m["wins"] / m["trades"] * 100 if m["trades"] else 0
    print(f"{month:10s} {m['trades']:7d} {m['wins']:6d} {win_pct:5.0f}% {m['pnl']:12,.2f}")
    total_pnl += m["pnl"]; total_trades += m["trades"]; total_wins += m["wins"]

win_pct_total = total_wins / total_trades * 100 if total_trades else 0
print(f"{'-'*70}\n{'TOTAL':10s} {total_trades:7d} {total_wins:6d} {win_pct_total:5.0f}% {total_pnl:12,.2f}\n{'='*70}\n")

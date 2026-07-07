"""
Real-data 1-year (VIX data limit) NIFTY daily EMA5/20 futures crossover,
gated by VIX regime: only take/hold trades when VIX <= VIX_MAX (calm/trending,
low chop risk). Skip signals entirely when VIX > VIX_MAX.
"""
import csv, glob, zipfile, io, json
from collections import defaultdict

PROJECT = "/Users/nithish-prabhu/Downloads/intra-day"
LOT = 75
EMA_FAST, EMA_SLOW = 5, 20
ATR_PERIOD = 14
ATR_STOP_MULT = 1.5
RISK_PCT = 0.75
TOTAL_CAPITAL = 500000.0
VIX_MAX = 15.0  # only trade when VIX at/below this

vix_data = json.load(open(f"{PROJECT}/data/india_vix_1y.json"))
vix_by_date = {row["date"]: row["close"] for row in vix_data}

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
                    "open": float(front["OpnPric"]), "high": float(front["HghPric"]),
                    "low": float(front["LwPric"]), "close": float(front["ClsPric"]),
                }

# restrict to dates where we have VIX
dates = sorted(d for d in bars if d in vix_by_date)
closes = [bars[d]["close"] for d in dates]


def ema_series(values, period):
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out

# compute EMAs on the FULL 2yr close series (need warm-up before the VIX window),
# then map back onto vix-covered dates only.
all_dates = sorted(bars.keys())
all_closes = [bars[d]["close"] for d in all_dates]
ema_fast_full = ema_series(all_closes, EMA_FAST)
ema_slow_full = ema_series(all_closes, EMA_SLOW)
idx_of = {d: i for i, d in enumerate(all_dates)}

tr = [bars[all_dates[0]]["high"] - bars[all_dates[0]]["low"]]
for i in range(1, len(all_dates)):
    h, l, pc = bars[all_dates[i]]["high"], bars[all_dates[i]]["low"], all_closes[i - 1]
    tr.append(max(h - l, abs(h - pc), abs(l - pc)))
atr_full = [None] * len(all_dates)
for i in range(len(all_dates)):
    if i + 1 >= ATR_PERIOD:
        atr_full[i] = sum(tr[i + 1 - ATR_PERIOD:i + 1]) / ATR_PERIOD

trades = []
position = None

start_i = max(idx_of[dates[0]], EMA_SLOW + 1)
for i in range(start_i, len(all_dates)):
    d = all_dates[i]
    if d not in vix_by_date:
        continue
    vix = vix_by_date[d]
    price = all_closes[i]
    cross_up = ema_fast_full[i - 1] <= ema_slow_full[i - 1] and ema_fast_full[i] > ema_slow_full[i]
    cross_down = ema_fast_full[i - 1] >= ema_slow_full[i - 1] and ema_fast_full[i] < ema_slow_full[i]
    calm = vix <= VIX_MAX

    if position:
        direction_mult = 1 if position["dir"] == "BUY" else -1
        hit_sl = (position["dir"] == "BUY" and bars[d]["low"] <= position["sl"]) or \
                 (position["dir"] == "SELL" and bars[d]["high"] >= position["sl"])
        reverse = (position["dir"] == "BUY" and cross_down) or (position["dir"] == "SELL" and cross_up)
        regime_exit = not calm  # force flat if VIX spikes above threshold

        if hit_sl or reverse or regime_exit:
            exit_price = position["sl"] if hit_sl else price
            pnl = (exit_price - position["entry_price"]) * direction_mult * position["qty"]
            reason = "SL" if hit_sl else ("REVERSE" if reverse else "VIX_SPIKE")
            trades.append({"dir": position["dir"], "entry_date": position["entry_date"], "exit_date": d,
                            "entry_price": position["entry_price"], "exit_price": exit_price,
                            "reason": reason, "qty": position["qty"], "pnl": round(pnl, 2)})
            position = None

    if position is None and atr_full[i] and calm and (cross_up or cross_down):
        direction = "BUY" if cross_up else "SELL"
        sl_dist = max(atr_full[i] * ATR_STOP_MULT, 20.0)
        risk_budget = TOTAL_CAPITAL * (RISK_PCT / 100.0)
        lots = max(1, int(risk_budget // (sl_dist * LOT)))
        qty = LOT * lots
        sl = price - sl_dist if direction == "BUY" else price + sl_dist
        position = {"dir": direction, "entry_date": d, "entry_price": price, "sl": sl, "qty": qty}

if position:
    d = all_dates[-1]
    direction_mult = 1 if position["dir"] == "BUY" else -1
    pnl = (all_closes[-1] - position["entry_price"]) * direction_mult * position["qty"]
    trades.append({"dir": position["dir"], "entry_date": position["entry_date"], "exit_date": d,
                    "entry_price": position["entry_price"], "exit_price": all_closes[-1],
                    "reason": "EOD_OPEN", "qty": position["qty"], "pnl": round(pnl, 2)})

print(f"\n{'='*100}")
print(f"NIFTY EMA5/20 FUTURES + VIX<= {VIX_MAX} REGIME FILTER ({dates[0]} to {dates[-1]})")
print(f"{'='*100}")
for t in trades:
    print(f"{t['dir']:4s} {t['entry_date']:11s} {t['exit_date']:11s} {t['reason']:10s} "
          f"{t['entry_price']:9.1f} {t['exit_price']:9.1f} {t['qty']:5d} {t['pnl']:10.2f}")

monthly = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})
for t in trades:
    month = t["entry_date"][:7]
    monthly[month]["pnl"] += t["pnl"]; monthly[month]["trades"] += 1
    if t["pnl"] > 0: monthly[month]["wins"] += 1

print(f"\n{'Month':10s} {'Trades':>7s} {'Wins':>6s} {'Win%':>6s} {'Net PnL':>12s}")
total_pnl, total_trades, total_wins = 0.0, 0, 0
for month in sorted(monthly.keys()):
    m = monthly[month]
    win_pct = m["wins"] / m["trades"] * 100 if m["trades"] else 0
    print(f"{month:10s} {m['trades']:7d} {m['wins']:6d} {win_pct:5.0f}% {m['pnl']:12,.2f}")
    total_pnl += m["pnl"]; total_trades += m["trades"]; total_wins += m["wins"]

win_pct_total = total_wins / total_trades * 100 if total_trades else 0
print(f"{'-'*70}\n{'TOTAL':10s} {total_trades:7d} {total_wins:6d} {win_pct_total:5.0f}% {total_pnl:12,.2f}")

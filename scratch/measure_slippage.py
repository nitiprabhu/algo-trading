"""
EXECUTION-SLIPPAGE LOGGER — the decisive real-world measurement.

The whole investigation reduces to ONE number: actual bid-ask slippage per leg on the
NIFTY weekly spread strikes. Backtest edge survives only if effective slippage stays
under ~0.4-0.5 pt/leg (see scratch/oos_costs_skew.py). This script samples LIVE quotes,
computes top-of-book half-spread AND the effective fill walking the book for your order
size, and appends to a CSV — run it repeatedly during market hours (e.g. cron every
5 min, 10:00-15:00) to build the real distribution, not a one-off EOD snapshot.

Setup (one time):
  pip install kiteconnect
  export KITE_API_KEY=...           # your Kite Connect app api_key
  export KITE_ACCESS_TOKEN=...      # daily access token (from your login flow)
Run:
  python scratch/measure_slippage.py            # one sample -> appends data/slippage_log.csv
Cron (market hours, weekdays):
  */5 10-14 * * 1-5  cd <repo> && python scratch/measure_slippage.py >> data/slip.out 2>&1

Reports per leg: best bid/ask, top-of-book half-spread, and effective half-slippage for
ORDER_LOTS lots (walking depth). Logs avg/leg and the 4-leg(spread) & 8-leg(condor)
round-trip slippage in points, flagged vs the 0.45pt breakeven.
"""
import os, csv, datetime, sys
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ORDER_LOTS = 3                 # size you'd actually trade — walks the book to here
SHORT_OTM_PCT = 1.0           # short strike ~1% OTM (~0.20 delta weekly proxy)
WING_OTM_PCT = 2.0            # long wing ~2% OTM (~0.10 delta proxy)
BREAKEVEN_PT = 0.45          # per-leg slippage above which the backtest edge dies
LOG = "data/slippage_log.csv"


def half_spread(depth):
    """Top-of-book half-spread (pts) and mid from a Kite depth dict."""
    bid = depth["buy"][0]["price"] if depth["buy"] and depth["buy"][0]["price"] > 0 else None
    ask = depth["sell"][0]["price"] if depth["sell"] and depth["sell"][0]["price"] > 0 else None
    if not bid or not ask:
        return None, None, bid, ask
    return (ask - bid) / 2.0, (ask + bid) / 2.0, bid, ask


def effective_half(depth, side, qty, mid):
    """VWAP fill price for `qty` walking the book, minus mid = effective half-slippage (pts).
    side 'buy' = you BUY (lift offers); 'sell' = you SELL (hit bids)."""
    book = depth["sell"] if side == "buy" else depth["buy"]
    need, cost = qty, 0.0
    for lvl in book:
        if lvl["price"] <= 0 or lvl["quantity"] <= 0:
            continue
        take = min(need, lvl["quantity"])
        cost += take * lvl["price"]; need -= take
        if need <= 0:
            break
    if need > 0 or mid is None:
        return None
    vwap = cost / qty
    return abs(vwap - mid)


def main():
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        sys.exit("pip install kiteconnect")
    api, tok = os.getenv("KITE_API_KEY"), os.getenv("KITE_ACCESS_TOKEN")
    if not api or not tok:
        sys.exit("set KITE_API_KEY and KITE_ACCESS_TOKEN")
    kite = KiteConnect(api_key=api); kite.set_access_token(tok)

    spot = kite.ltp(["NSE:NIFTY 50"])["NSE:NIFTY 50"]["last_price"]
    # nearest NIFTY weekly expiry from the live NFO instrument dump
    insts = [i for i in kite.instruments("NFO")
             if i["name"] == "NIFTY" and i["segment"] == "NFO-OPT"]
    today = datetime.datetime.now(IST).date()
    future = sorted({i["expiry"] for i in insts if i["expiry"] >= today})
    if not future:
        sys.exit("no future NIFTY expiry found")
    expiry = future[0]
    step = 50
    def near(pct, up):
        raw = spot * (1 + (pct/100.0)*(1 if up else -1))
        return round(raw / step) * step
    legs = {
        "short_PE": (near(SHORT_OTM_PCT, False), "PE", "sell"),
        "wing_PE":  (near(WING_OTM_PCT, False), "PE", "buy"),
        "short_CE": (near(SHORT_OTM_PCT, True),  "CE", "sell"),
        "wing_CE":  (near(WING_OTM_PCT, True),  "CE", "buy"),
    }
    sym = {}
    for k, (strike, opt, _) in legs.items():
        m = [i for i in insts if i["expiry"] == expiry and i["strike"] == strike
             and i["instrument_type"] == opt]
        if not m:
            sys.exit(f"strike {strike}{opt} {expiry} not listed")
        sym[k] = "NFO:" + m[0]["tradingsymbol"]
    q = kite.quote(list(sym.values()))

    lot = next(i["lot_size"] for i in insts if i["expiry"] == expiry)
    qty = ORDER_LOTS * lot
    rows = []
    tot_top = tot_eff = 0.0
    for k, (strike, opt, side) in legs.items():
        d = q[sym[k]]["depth"]
        hs, mid, bid, ask = half_spread(d)
        eff = effective_half(d, side, qty, mid)
        rows.append((k, strike, opt, side, bid, ask, hs, eff))
        if hs is not None: tot_top += hs
        if eff is not None: tot_eff += eff
    n = len(rows)
    avg_top, avg_eff = tot_top/n, tot_eff/n
    now = datetime.datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{now} | NIFTY {spot} | weekly {expiry} | size {ORDER_LOTS} lots ({qty})")
    print(f"{'leg':9} {'strike':>7} {'bid':>8} {'ask':>8} {'top½':>6} {'eff½(sz)':>9}")
    for k, st, opt, side, bid, ask, hs, eff in rows:
        print(f"{k:9} {st:>6}{opt} {str(bid):>8} {str(ask):>8} "
              f"{(f'{hs:.2f}' if hs else '-'):>6} {(f'{eff:.2f}' if eff else '-'):>9}")
    print(f"\navg/leg: top-of-book {avg_top:.3f}pt | effective@{ORDER_LOTS}lots {avg_eff:.3f}pt "
          f"(breakeven ~{BREAKEVEN_PT}pt)")
    verdict = "EDGE SURVIVES" if avg_eff <= BREAKEVEN_PT else "edge dies"
    print(f"spread(4-leg) RT slip {4*avg_eff:.2f}pt | condor(8-leg) {8*avg_eff:.2f}pt -> {verdict}")

    os.makedirs("data", exist_ok=True)
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts","spot","expiry","lots","avg_top_half","avg_eff_half",
                        *[f"{k}_top" for k in legs], *[f"{k}_eff" for k in legs]])
        w.writerow([now, spot, expiry, ORDER_LOTS, round(avg_top,3), round(avg_eff,3),
                    *[round(r[6],3) if r[6] else "" for r in rows],
                    *[round(r[7],3) if r[7] else "" for r in rows]])
    print(f"logged -> {LOG}")


if __name__ == "__main__":
    main()

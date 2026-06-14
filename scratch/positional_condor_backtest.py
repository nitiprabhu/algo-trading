"""
POSITIONAL weekly iron condor — held to weekly expiry (multi-day theta).

The decisive test for premium-selling: intraday selling fails (5h ≈ no theta).
Here we ENTER a fresh weekly condor each cycle and HOLD to expiry, so theta actually
accrues over 4-6 days. Win when spot finishes inside the short strikes by expiry.

Cycle: weekly expiry = Thursday (or prior trading day if holiday). Entry = first trading
day after the previous expiry (full weekly cycle, max theta). Mark daily on close; optional
stop / profit-take on the daily mark; otherwise settle at expiry intrinsic.

Pricing: Black-Scholes, ANNUALIZED vol (vix/100) — NOT iv_from_vix (which double-scales).
Costs on all 4 legs, entry + exit. Defined risk via long wings.

Spot path: accumulate continuous 15-min candles across Mar-May by replaying each day.

Usage: PYTHONDONTWRITEBYTECODE=1 python scratch/positional_condor_backtest.py
"""
import os
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["DATABASE_URL"] = ""
import services.chartedge_core.config as cm
cm.apply_db_overrides = lambda c: None

import asyncio
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime
from services.chartedge_core.option_data import bs_price, bs_delta

IST = ZoneInfo("Asia/Kolkata")

# --- params ---
SHORT_DELTA = 0.20       # positional: give more room than intraday
WING_DELTA = 0.10
PROFIT_TAKE = 0.55       # close cycle if condor buy-back <= (1-PT)*credit
STOP_MULT = 2.2          # close cycle if daily mark >= STOP_MULT*credit
EXPIRY_WEEKDAY = 3       # Thursday weekly expiry
CAPITAL = 500000.0
RISK_PER_TRADE = 0.02 * CAPITAL
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100}
LOT_SIZE = {"NIFTY": 25, "BANKNIFTY": 15}
MAX_LOTS = {"NIFTY": 4, "BANKNIFTY": 3}


def ann_iv(vix):
    return max(0.05, vix / 100.0)


def pick_strike(spot, dte, iv, opt_type, target_delta, direction):
    step = 50 if spot < 40000 else 100
    k = round(spot / step) * step
    for _ in range(120):
        k += direction * step
        if k <= 0:
            break
        if abs(bs_delta(spot, k, dte, iv, opt_type)) <= target_delta:
            return k
    return k


def condor_value(spot, dte, iv, strikes):
    sp_pe, lp_pe, sc_ce, lc_ce = strikes
    return ((bs_price(spot, sp_pe, dte, iv, "PE") - bs_price(spot, lp_pe, dte, iv, "PE"))
            + (bs_price(spot, sc_ce, dte, iv, "CE") - bs_price(spot, lc_ce, dte, iv, "CE")))


async def collect_daily(rt):
    """Replay Mar-May day by day; build per-symbol per-date {open,close} + vix."""
    daily = {"NIFTY": {}, "BANKNIFTY": {}}
    vix_by_date = {}
    trading_days = []
    for (y, m, dmax) in [(2026, 3, 31), (2026, 4, 30), (2026, 5, 19)]:
        for d in range(1, dmax + 1):
            try:
                chk = datetime(y, m, d, 9, 0, tzinfo=IST)
            except ValueError:
                continue
            if not rt._is_trading_day(chk):
                continue
            try:
                await rt.run_backtest(datetime(y, m, d, 9, 0, tzinfo=IST),
                                      datetime(y, m, d, 15, 30, tzinfo=IST),
                                      run_regime_agent=False)
            except Exception:
                continue
            dt = chk.date()
            got = False
            for sym in ("NIFTY", "BANKNIFTY"):
                cs = sorted([c for c in rt.candles.get(sym, []) if c.time.date() == dt],
                            key=lambda c: c.time)
                if len(cs) >= 5:
                    daily[sym][dt] = {"open": cs[0].open, "close": cs[-1].close}
                    got = True
            vc = [c for c in rt.candles.get("INDIAVIX", []) if c.time.date() == dt]
            vix_by_date[dt] = vc[-1].close if vc else 14.0
            if got:
                trading_days.append(dt)
    return daily, vix_by_date, sorted(set(trading_days))


def build_cycles(trading_days):
    """Group trading days into weekly cycles ending on the Thursday-expiry (or last day <= Thu)."""
    cycles = []
    cur = []
    for d in trading_days:
        cur.append(d)
        # expiry = this day is Thursday, OR next trading day jumps past a Thursday
        is_expiry = (d.weekday() == EXPIRY_WEEKDAY)
        if is_expiry:
            cycles.append(cur)
            cur = []
    if cur:
        cycles.append(cur)
    # keep cycles with >=2 days (need entry + hold)
    return [c for c in cycles if len(c) >= 2]


def main_sync(daily, vix_by_date, trading_days):
    cycles = build_cycles(trading_days)
    results = {}
    cyc_rows = []
    for cyc in cycles:
        entry_d = cyc[0]
        expiry_d = cyc[-1]
        for sym in ("NIFTY", "BANKNIFTY"):
            if entry_d not in daily[sym] or expiry_d not in daily[sym]:
                continue
            spot0 = daily[sym][entry_d]["open"]
            vix0 = vix_by_date.get(entry_d, 14.0)
            iv0 = ann_iv(vix0)
            dte0 = max(1.0, (expiry_d - entry_d).days + 0.3)
            sp = pick_strike(spot0, dte0, iv0, "PE", SHORT_DELTA, -1)
            lp = pick_strike(spot0, dte0, iv0, "PE", WING_DELTA, -1)
            sc = pick_strike(spot0, dte0, iv0, "CE", SHORT_DELTA, +1)
            lc = pick_strike(spot0, dte0, iv0, "CE", WING_DELTA, +1)
            strikes = (sp, lp, sc, lc)
            credit = condor_value(spot0, dte0, iv0, strikes)
            if credit <= 1.0:
                continue
            put_w, call_w = sp - lp, lc - sc
            max_loss = max(put_w, call_w) - credit
            if max_loss <= 0:
                continue
            lot = LOT_SIZE[sym]
            lots = max(1, min(int(RISK_PER_TRADE / (max_loss * lot)), MAX_LOTS[sym]))
            qty = lots * lot
            cost = round(0.02 * credit * qty + 8 * lots, 2)

            # mark daily across the cycle (days after entry, up to & incl expiry)
            exit_val, reason = None, "EXPIRY"
            for d in cyc[1:]:
                if d not in daily[sym]:
                    continue
                spot = daily[sym][d]["close"]
                dte_t = max(0.02, (expiry_d - d).days + (0.3 if d != expiry_d else 0.0))
                iv_t = ann_iv(vix_by_date.get(d, vix0))
                val = condor_value(spot, dte_t, iv_t, strikes)
                if d == expiry_d:
                    # settle at intrinsic
                    val = condor_value(spot, 0.0, iv_t, strikes)
                    val = min(val, max_loss + credit)
                    exit_val, reason = val, "EXPIRY"
                    break
                if val <= (1 - PROFIT_TAKE) * credit:
                    exit_val, reason = val, "PT"
                    break
                if val >= STOP_MULT * credit:
                    exit_val, reason = min(val, max_loss + credit), "STOP"
                    break
            if exit_val is None:
                continue
            pnl = (credit - exit_val) * qty - cost
            results[expiry_d.month] = results.get(expiry_d.month, 0.0) + pnl
            cyc_rows.append((str(entry_d), str(expiry_d), sym, round(credit, 1),
                             round(exit_val, 1), reason, round(pnl, 0)))

    print("\n==== POSITIONAL WEEKLY IRON CONDOR (held to expiry) — Mar-May 2026 ====")
    print(f"params: short Δ{SHORT_DELTA}/wing Δ{WING_DELTA}, PT {PROFIT_TAKE:.0%}, stop {STOP_MULT}x, weekly Thu expiry")
    print(f"{'entry':11} {'expiry':11} {'sym':9} {'cr':>6} {'exit':>6} {'why':6} {'pnl':>9}")
    for r in cyc_rows:
        print(f"{r[0]:11} {r[1]:11} {r[2]:9} {r[3]:>6} {r[4]:>6} {r[5]:6} {r[6]:>9.0f}")
    wins = [r[6] for r in cyc_rows if r[6] > 0]
    losses = [r[6] for r in cyc_rows if r[6] <= 0]
    print("\n-- by month --")
    for mth in sorted(results):
        nm = {3: "March", 4: "April", 5: "May"}[mth]
        print(f"  {nm:6}: ₹{results[mth]:>10.0f}")
    total = sum(results.values())
    print(f"\nTOTAL 3-month: ₹{total:.0f}")
    n = len(cyc_rows)
    if n:
        print(f"Cycles: {n} | win {len(wins)} ({len(wins)/n*100:.0f}%) | "
              f"avg win ₹{(sum(wins)/len(wins) if wins else 0):.0f} | "
              f"avg loss ₹{(sum(losses)/len(losses) if losses else 0):.0f}")
    print("\nCompare — INTRADAY buying -3,677 | INTRADAY selling ~-24,000 (same data)")


async def main():
    cfg = load_config(os.path.abspath("shared/config.yaml"))
    rt = IndstocksMarketRuntime(cfg, skip_db_load=True)
    daily, vix_by_date, trading_days = await collect_daily(rt)
    main_sync(daily, vix_by_date, trading_days)


if __name__ == "__main__":
    asyncio.run(main())

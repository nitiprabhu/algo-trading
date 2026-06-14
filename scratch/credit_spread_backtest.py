"""
Intraday Iron Condor (credit-spread) backtest — PATH 2.

Premium SELLING, the inverse of the buying engine: win small consistently from
theta + range-bound days, lose (capped) on trend days. Tests whether selling beats
buying on the same Mar-May 2026 data — especially the chop months (Mar, Apr) where
buying bled.

Structure (per index, once/day):
  - Short PE ~target_delta below spot, long PE ~wing_delta further below.
  - Short CE ~target_delta above spot, long CE ~wing_delta further above.
  - Net credit collected up front. Defined risk = max(call_width, put_width) - credit.
Exit:
  - Take profit when condor can be bought back at (1-profit_take)*credit.
  - Stop when condor mark = stop_mult * credit (loss).
  - Else square off at 15:15 (capture remaining decay).
Pricing: Black-Scholes (option_data.bs_price/bs_delta/iv_from_vix) — same model as the
buying backtest, so results are comparable. Intraday theta via DTE decaying over the day.
Costs: per-leg brokerage+STT+spread on all 4 legs, entry and exit.

Deterministic. Reuses runtime only to fetch the underlying candle series.

Usage: PYTHONDONTWRITEBYTECODE=1 python scratch/credit_spread_backtest.py
"""
import os
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["DATABASE_URL"] = ""
import services.chartedge_core.config as cm
cm.apply_db_overrides = lambda c: None

import asyncio
from datetime import datetime, time
from zoneinfo import ZoneInfo
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime
from services.chartedge_core.option_data import bs_price, bs_delta

IST = ZoneInfo("Asia/Kolkata")


def ann_iv(vix: float) -> float:
    """Annualized vol for BS. iv_from_vix double-scales by DTE (then bs_price scales by
    sqrt(T) again) → vol far too low and strikes hug spot. BS wants the ANNUALIZED vol;
    its own sqrt(T) handles the horizon. India VIX is already an annualized 30d vol."""
    return max(0.05, vix / 100.0)

# --- Strategy params ---
ENTRY_TIME = time(9, 45)
EXIT_TIME = time(15, 15)
DTE_ENTRY = 3.0          # days to expiry at entry (weekly cycle, mid-week). Theta source.
TARGET_DELTA = 0.25      # short strike delta (~75% prob OTM)
WING_DELTA = 0.12        # long strike delta (defined risk)
PROFIT_TAKE = 0.50       # buy back at 50% of credit captured
STOP_MULT = 2.0          # stop when condor worth 2x credit (loss)
CAPITAL = 500000.0
RISK_PER_TRADE = 0.02 * CAPITAL   # ₹10k max defined risk per condor
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100}
LOT_SIZE = {"NIFTY": 25, "BANKNIFTY": 15}
MAX_LOTS = {"NIFTY": 4, "BANKNIFTY": 3}
# Per-leg round-trip cost approximation (brokerage+STT+exchange+spread), ₹ per unit premium-notionalish.
# Use a flat ₹ per leg per lot-unit: model as spread ticks. Keep simple & conservative.
COST_PER_LEG_PER_UNIT = 0.0  # set below via spread model


def _norm_spot_strikes(spot, step):
    base = round(spot / step) * step
    return base


def pick_strike(spot, dte, iv, opt_type, target_delta, direction):
    """Walk strikes away from spot until |delta| <= target_delta. direction: +1 above, -1 below."""
    step = 50 if spot < 40000 else 100
    k = _norm_spot_strikes(spot, step)
    for _ in range(80):
        k += direction * step
        if k <= 0:
            break
        d = abs(bs_delta(spot, k, dte, iv, opt_type))
        if d <= target_delta:
            return k
    return k


def condor_value(spot, dte, iv, strikes):
    """Mark-to-market value (cost to buy back) of the short iron condor."""
    sp_pe, lp_pe, sc_ce, lc_ce = strikes
    short_pe = bs_price(spot, sp_pe, dte, iv, "PE")
    long_pe = bs_price(spot, lp_pe, dte, iv, "PE")
    short_ce = bs_price(spot, sc_ce, dte, iv, "CE")
    long_ce = bs_price(spot, lc_ce, dte, iv, "CE")
    # We are short the inner, long the outer. Net liability to close = (short - long) both sides.
    return (short_pe - long_pe) + (short_ce - long_ce)


async def main():
    cfg = load_config(os.path.abspath("shared/config.yaml"))
    rt = IndstocksMarketRuntime(cfg, skip_db_load=True)

    months = [(2026, 3, 31), (2026, 4, 30), (2026, 5, 19)]
    results = {}   # month -> pnl
    all_days = []

    for (y, m, dmax) in months:
        for d in range(1, dmax + 1):
            try:
                chk = datetime(y, m, d, 9, 0, tzinfo=IST)
            except ValueError:
                continue
            if not rt._is_trading_day(chk):
                continue
            s = datetime(y, m, d, 9, 0, tzinfo=IST)
            e = datetime(y, m, d, 15, 30, tzinfo=IST)
            try:
                await rt.run_backtest(s, e, run_regime_agent=False)
            except Exception:
                continue

            vixc = [c for c in rt.candles.get("INDIAVIX", []) if c.time.date() == chk.date()]
            vix = vixc[-1].close if vixc else 14.0

            day_pnl = 0.0
            for sym in ("NIFTY", "BANKNIFTY"):
                cands = sorted(
                    [c for c in rt.candles.get(sym, []) if c.time.date() == chk.date()],
                    key=lambda c: c.time,
                )
                if len(cands) < 10:
                    continue
                # entry candle
                entry = next((c for c in cands if c.time.time() >= ENTRY_TIME), None)
                if entry is None:
                    continue
                t0 = entry.time
                spot0 = entry.open
                iv0 = ann_iv(vix)
                step = STRIKE_STEP[sym]
                # strikes
                sp_pe = pick_strike(spot0, DTE_ENTRY, iv0, "PE", TARGET_DELTA, -1)
                lp_pe = pick_strike(spot0, DTE_ENTRY, iv0, "PE", WING_DELTA, -1)
                sc_ce = pick_strike(spot0, DTE_ENTRY, iv0, "CE", TARGET_DELTA, +1)
                lc_ce = pick_strike(spot0, DTE_ENTRY, iv0, "CE", WING_DELTA, +1)
                strikes = (sp_pe, lp_pe, sc_ce, lc_ce)
                credit = condor_value(spot0, DTE_ENTRY, iv0, strikes)
                if credit <= 1.0:
                    continue
                put_w = sp_pe - lp_pe
                call_w = lc_ce - sc_ce
                max_loss_per_unit = max(put_w, call_w) - credit
                if max_loss_per_unit <= 0:
                    continue
                lot = LOT_SIZE[sym]
                lots_by_risk = int(RISK_PER_TRADE / (max_loss_per_unit * lot))
                lots = max(1, min(lots_by_risk, MAX_LOTS[sym]))
                qty = lots * lot
                # per-leg cost: model bid-ask spread cost ~ 1.5% of leg premium round trip + flat
                # 4 legs in, 4 legs out. Approximate as 2% of credit notional round-trip.
                cost = round(0.02 * (abs(credit)) * qty + 4 * 2 * lots * 1.0, 2)

                # intraday MTM walk
                exit_val = None
                exit_reason = "EOD"
                for c in cands:
                    if c.time <= t0:
                        continue
                    if c.time.time() >= EXIT_TIME:
                        break
                    hrs = (c.time - t0).total_seconds() / 3600.0
                    dte_t = max(0.05, DTE_ENTRY - hrs / 24.0)
                    iv_t = ann_iv(vix)
                    val = condor_value(c.close, dte_t, iv_t, strikes)
                    if val <= (1 - PROFIT_TAKE) * credit:
                        exit_val = val
                        exit_reason = "PT"
                        break
                    if val >= STOP_MULT * credit:
                        exit_val = min(val, max_loss_per_unit + credit)  # cap at defined risk
                        exit_reason = "STOP"
                        break
                if exit_val is None:
                    # square off at EOD
                    last = cands[-1]
                    hrs = (last.time - t0).total_seconds() / 3600.0
                    dte_t = max(0.02, DTE_ENTRY - hrs / 24.0)
                    exit_val = condor_value(last.close, dte_t, ann_iv(vix), strikes)

                # P&L: sold for credit, buy back for exit_val. Profit = (credit - exit_val) * qty - cost
                pnl = (credit - exit_val) * qty - cost
                day_pnl += pnl

            if day_pnl != 0.0:
                all_days.append((f"{y}-{m:02d}-{d:02d}", round(day_pnl, 0)))
                results[m] = results.get(m, 0.0) + day_pnl

    print("\n==== INTRADAY IRON CONDOR (credit-spread) — Mar-May 2026 ====")
    print(f"params: short Δ{TARGET_DELTA} / wing Δ{WING_DELTA}, PT {PROFIT_TAKE:.0%}, stop {STOP_MULT}x, DTE {DTE_ENTRY}")
    print(f"{'DAY':12} | {'PnL':>9}")
    for dt, p in all_days:
        print(f"{dt:12} | {p:>9.0f}")
    wins = [p for _, p in all_days if p > 0]
    losses = [p for _, p in all_days if p <= 0]
    n = len(all_days)
    print("\n-- by month --")
    for mth in sorted(results):
        nm = {3: "March", 4: "April", 5: "May"}[mth]
        print(f"  {nm:6}: ₹{results[mth]:>10.0f}")
    total = sum(results.values())
    print(f"\nTOTAL 3-month: ₹{total:.0f}")
    if n:
        print(f"Win-days: {len(wins)}/{n} ({len(wins)/n*100:.0f}%) | "
              f"avg win ₹{(sum(wins)/len(wins) if wins else 0):.0f} | "
              f"avg loss ₹{(sum(losses)/len(losses) if losses else 0):.0f}")
    print("\nCompare — BUYING engine (same data): March -6,977 | April -6,902 | May +10,202 | TOTAL -3,677")


if __name__ == "__main__":
    asyncio.run(main())

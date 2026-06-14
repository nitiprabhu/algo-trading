"""
MFE (Max Favorable Excursion) analysis for option trades.

Question being answered: when option-buying trades win/lose, how far do they
ACTUALLY run in our favor before exit? If winners routinely reach +20/30% MFE
but we bank far less, the exit logic is leaking edge and "let winners run" helps.
If MFE tops out near the exit level, there is no fat tail to capture in this
regime and buying genuinely has no edge — no exit tweak will fix it.

Deterministic: AI keys blanked (rule_based signals), apply_db_overrides no-op'd
(yaml authoritative), fixed confluence threshold. Fully reproducible.

Usage:
    PYTHONDONTWRITEBYTECODE=1 python scratch/mfe_analysis.py --start 2026-05-01 --end 2026-05-19 --fixed 0.50
    PYTHONDONTWRITEBYTECODE=1 python scratch/mfe_analysis.py --start 2026-04-01 --end 2026-04-30 --fixed 0.50
"""
import asyncio
import os
import sys
import argparse
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# --- DETERMINISM SETUP (must happen before chartedge imports use them) ---
# 1. Blank AI keys → ai_signal falls back to rule_based (no LLM non-determinism)
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
# 2. Neutralize DB config override so shared/config.yaml is authoritative
os.environ["DATABASE_URL"] = ""

import services.chartedge_core.config as config_module
# Hard no-op the DB override layer — yaml is the single source of truth here
config_module.apply_db_overrides = lambda c: None

from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")


def is_option(sym: str) -> bool:
    return any(x in sym for x in ("-CE", "-PE", "_CE", "_PE"))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-05-01")
    ap.add_argument("--end", default="2026-05-19")
    ap.add_argument("--fixed", type=float, default=0.50)
    ap.add_argument("--keep", type=float, default=None, help="options_trail_keep_frac override")
    ap.add_argument("--arm", type=float, default=None, help="options_trail_arm_pct override")
    ap.add_argument("--minsep", type=float, default=None, help="options_min_trend_sep_pct override")
    ap.add_argument("--out", default="scratch/mfe_trades.json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    start_target = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_target = datetime.strptime(args.end, "%Y-%m-%d").date()

    config = load_config(os.path.abspath("shared/config.yaml"))
    if args.keep is not None:
        config.risk["options_trail_keep_frac"] = args.keep
    if args.arm is not None:
        config.risk["options_trail_arm_pct"] = args.arm
    if getattr(args, "minsep", None) is not None:
        config.risk["options_min_trend_sep_pct"] = args.minsep
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)

    print(f"\n{'='*80}")
    print(f"MFE ANALYSIS {args.start}→{args.end} | fixed threshold={args.fixed} | rule_based (no AI)")
    print(f"{'='*80}\n")

    all_trades = []
    cur = start_target
    while cur <= end_target:
        check_dt = datetime.combine(cur, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
        if not runtime._is_trading_day(check_dt):
            cur += timedelta(days=1)
            continue
        start_dt = datetime.combine(cur, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
        end_dt = datetime.combine(cur, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)
        for k in list(runtime.signal_engine.thresholds.keys()):
            runtime.signal_engine.thresholds[k] = args.fixed
        runtime.signal_engine.thresholds["DEFAULT"] = args.fixed
        try:
            res = await runtime.run_backtest(start_dt, end_dt, run_regime_agent=False)
            if res.get("status") == "error":
                print(f"⚠️ {cur}: {res.get('reason')}")
            else:
                all_trades.extend(runtime.trader.closed_trades.copy())
        except Exception as e:
            print(f"⚠️ {cur} failed: {e}")
        await asyncio.sleep(0.2)
        cur += timedelta(days=1)

    # Keep only option trades
    opt = [t for t in all_trades if is_option(t.instrument)]
    rows = []
    for t in opt:
        rows.append({
            "instrument": t.instrument,
            "side": "CE" if ("-CE" in t.instrument or "_CE" in t.instrument) else "PE",
            "entry_time": t.entry_time.strftime("%Y-%m-%d %H:%M"),
            "exit_reason": t.exit_reason,
            "pnl": round(t.pnl, 2),
            "pnl_pct": round(t.pnl_pct, 2),
            "mfe_pct": round(t.highest_pnl_pct, 2),   # max favorable excursion
            "left_on_table": round(t.highest_pnl_pct - t.pnl_pct, 2),
        })
    with open(args.out, "w") as f:
        json.dump(rows, f, indent=2)

    if not rows:
        print("No option trades.")
        return

    # --- ANALYSIS ---
    n = len(rows)
    winners = [r for r in rows if r["pnl"] > 0]
    losers = [r for r in rows if r["pnl"] <= 0]
    wr = len(winners) / n * 100

    def avg(xs, k):
        return sum(x[k] for x in xs) / len(xs) if xs else 0.0

    print(f"Option trades: {n} | WR {wr:.1f}% ({len(winners)}W/{len(losers)}L)")
    print(f"Avg winner pnl%: {avg(winners,'pnl_pct'):+.2f} | Avg loser pnl%: {avg(losers,'pnl_pct'):+.2f}")
    print(f"Total PnL: ₹{sum(r['pnl'] for r in rows):,.0f}\n")

    # MFE distribution across ALL trades — did price ever run our way?
    bins = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 50), (50, 1000)]
    print("MFE distribution (peak unrealized gain reached, ALL trades):")
    print(f"  {'bucket':>10} | {'count':>5} | {'% of trades':>11}")
    for lo, hi in bins:
        c = sum(1 for r in rows if lo <= r["mfe_pct"] < hi)
        label = f"{lo}-{hi}%" if hi < 1000 else f"{lo}%+"
        print(f"  {label:>10} | {c:>5} | {c/n*100:>10.1f}%")

    # The key leak metric: trades that reached >=15% MFE but we banked far less
    ran = [r for r in rows if r["mfe_pct"] >= 15]
    print(f"\nTrades reaching >=15% MFE: {len(ran)} ({len(ran)/n*100:.1f}%)")
    if ran:
        print(f"  ...of those, avg actually banked: {avg(ran,'pnl_pct'):+.2f}% "
              f"| avg left on table: {avg(ran,'left_on_table'):.2f}%")
    big = [r for r in rows if r["mfe_pct"] >= 25]
    print(f"Trades reaching >=25% MFE (real fat tail): {len(big)} ({len(big)/n*100:.1f}%)")

    print(f"\nVERDICT INPUT:")
    print(f"  If >=15% MFE rate is HIGH (>25%) and 'left on table' is big → exit leaks, #3 helps.")
    print(f"  If >=15% MFE rate is LOW (<15%) → no fat tail in this regime, buying has no edge.")
    print(f"\nWrote per-trade detail → {args.out}")


if __name__ == "__main__":
    asyncio.run(main())

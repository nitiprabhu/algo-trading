#!/usr/bin/env python3
"""Apr–Jun backtest comparison: new futures stack vs pre-futures baseline."""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

IST = ZoneInfo("Asia/Kolkata")

# Pre-futures baseline (options + old futures) from fix_comparison.log
BASELINE = {
    "2026-04": {"options": 20516.90, "futures": 9954.00, "combined": 30470.90, "trades": 105},
    "2026-05": {"options": 18562.50, "futures": 12019.50, "combined": 30582.00, "trades": 91},
    "2026-06": {"options": 3515.90, "futures": 7509.75, "combined": 11025.65, "trades": 31},
}
BASELINE_TOTAL = sum(m["combined"] for m in BASELINE.values())

MONTHS = [
    ("2026-04", "2026-04-01", "2026-04-30"),
    ("2026-05", "2026-05-01", "2026-05-31"),
    ("2026-06", "2026-06-01", "2026-06-12"),
]


async def run_month(label: str, start: str, end: str) -> dict:
    from services.chartedge_core.config import load_config
    from services.chartedge_core.indstocks import IndstocksMarketRuntime

    config = load_config()
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.signal_engine.ai_enabled = False  # strategy-only, matches fix_comparison.log

    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(
        hour=15, minute=30, tzinfo=IST
    )

    print(f"\n>>> Running {label}...")
    result = await runtime.run_backtest(start_dt, end_dt, run_regime_agent=True)
    if result.get("status") != "ok":
        print(f"ERROR: {result}")
        sys.exit(1)

    opt_pnl = sum(t.pnl for t in runtime.trader.closed_trades)
    fut_pnl = sum(t.pnl for t in runtime.futures_trader.closed_trades)
    combined = opt_pnl + fut_pnl
    trades = len(runtime.trader.closed_trades) + len(runtime.futures_trader.closed_trades)
    closed = runtime.trader.closed_trades + [
        t.to_paper_trade() for t in runtime.futures_trader.closed_trades
    ]
    wins = sum(1 for t in closed if t.pnl > 0)
    win_pct = round(wins / trades * 100, 1) if trades else 0.0

    return {
        "options": round(opt_pnl, 2),
        "futures": round(fut_pnl, 2),
        "combined": round(combined, 2),
        "trades": trades,
        "win_pct": win_pct,
    }


async def main() -> None:
    # Use INDstocks historical feed (same source as fix_comparison.log).
    # Zerodha cache in data/zerodha_cache only covers a short May window.
    os.environ.pop("ZERODHA_CACHE_DIR", None)

    print("=" * 78)
    print("BACKTEST COMPARISON — NEW (ORB SL + futures costs + structure routing)")
    print("=" * 78)

    results: dict[str, dict] = {}
    for label, start, end in MONTHS:
        results[label] = await run_month(label, start, end)

    print("\n" + "=" * 78)
    print(f"{'Month':<10} {'Segment':<12} {'Baseline':>14} {'New':>14} {'Delta':>12}")
    print("-" * 78)

    new_total = 0.0
    for label in BASELINE:
        b = BASELINE[label]
        n = results[label]
        new_total += n["combined"]
        for seg, key in [("Options", "options"), ("Futures", "futures"), ("COMBINED", "combined")]:
            delta = n[key] - b[key]
            print(f"{label:<10} {seg:<12} {b[key]:>+14.2f} {n[key]:>+14.2f} {delta:>+12.2f}")
        print(f"{label:<10} {'Trades':<12} {b['trades']:>14} {n['trades']:>14} {n['trades'] - b['trades']:>+12}")
        print(f"{label:<10} {'Win%':<12} {'—':>14} {n['win_pct']:>13.1f}%")
        print("-" * 78)

    delta_total = new_total - BASELINE_TOTAL
    print(f"{'TOTAL':<10} {'COMBINED':<12} {BASELINE_TOTAL:>+14.2f} {new_total:>+14.2f} {delta_total:>+12.2f}")
    print("=" * 78)

    if new_total >= BASELINE_TOTAL:
        print("VERDICT: KEEP changes (new >= baseline)")
    else:
        print("VERDICT: REVERT changes (new < baseline)")

    sys.exit(0 if new_total >= BASELINE_TOTAL else 1)


if __name__ == "__main__":
    asyncio.run(main())

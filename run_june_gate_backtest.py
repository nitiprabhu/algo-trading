#!/usr/bin/env python3
"""June 2026 INDmoney backtest gate — keep changes only if combined PnL >= baseline."""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()
# Always INDmoney — never Zerodha cache
os.environ.pop("ZERODHA_CACHE_DIR", None)

IST = ZoneInfo("Asia/Kolkata")
JUNE_BASELINE_COMBINED = 29816.75  # fresh INDmoney run 2026-06-01..12 (pre live-gate changes)


async def run_june() -> dict:
    from services.chartedge_core.config import load_config
    from services.chartedge_core.indstocks import IndstocksMarketRuntime

    config = load_config()
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.signal_engine.ai_enabled = False

    start = datetime(2026, 6, 1, tzinfo=IST)
    end = datetime(2026, 6, 12, 15, 30, tzinfo=IST)

    print(">>> June 2026 backtest (INDmoney, AI off, regime agent on)...")
    result = await runtime.run_backtest(start, end, run_regime_agent=True)
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
    r = await run_june()
    baseline = JUNE_BASELINE_COMBINED
    profitable = r["combined"] > 0
    not_worse = r["combined"] >= baseline

    print("\n" + "=" * 60)
    print(f"June 2026 — Options:  ₹{r['options']:+,.2f}")
    print(f"June 2026 — Futures:  ₹{r['futures']:+,.2f}")
    print(f"June 2026 — Combined: ₹{r['combined']:+,.2f}  ({r['trades']} trades, {r['win_pct']}% win)")
    print(f"Baseline combined:    ₹{baseline:+,.2f}")
    print(f"Delta:                ₹{r['combined'] - baseline:+,.2f}")
    print("=" * 60)

    if profitable and not_worse:
        print("VERDICT: KEEP changes (profitable and >= baseline)")
        sys.exit(0)
    if profitable:
        print("VERDICT: REVERT (profitable but below baseline — do not degrade live edge)")
        sys.exit(1)
    print("VERDICT: REVERT (June not profitable)")
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

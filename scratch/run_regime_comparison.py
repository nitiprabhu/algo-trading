"""
Benchmark vs Regime-Agent Comparison (Apr-Jun 2026)
=====================================================
Runs three months twice:
  RUN A — NO regime agent  (replicates the locked ₹69,929.05 benchmark)
  RUN B — RULE-BASED regime agent only (no AI/LLM calls; deterministic)

Goal: see whether the rule-based regime classification improves or hurts
versus the current static-threshold benchmark.

Usage:
    PYTHONDONTWRITEBYTECODE=1 python scratch/run_regime_comparison.py
"""
import asyncio
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
os.environ.pop("ZERODHA_CACHE_DIR", None)   # always use INDmoney data

IST = ZoneInfo("Asia/Kolkata")


# ── helpers ──────────────────────────────────────────────────────────────────

def _summarise(runtime) -> dict:
    opt_pnl = sum(t.pnl for t in runtime.trader.closed_trades)
    fut_pnl = sum(t.pnl for t in runtime.futures_trader.closed_trades)
    combined = opt_pnl + fut_pnl
    trades = (len(runtime.trader.closed_trades)
              + len(runtime.futures_trader.closed_trades))
    closed = runtime.trader.closed_trades + [
        t.to_paper_trade() for t in runtime.futures_trader.closed_trades
    ]
    wins = sum(1 for t in closed if t.pnl > 0)
    win_pct = round(wins / trades * 100, 1) if trades else 0.0
    return {
        "options":  round(opt_pnl,  2),
        "futures":  round(fut_pnl,  2),
        "combined": round(combined, 2),
        "trades":   trades,
        "win_pct":  win_pct,
    }


async def run_month(start: datetime, end: datetime, *, use_regime: bool) -> dict:
    """
    Run a single month backtest.

    use_regime=False  →  static thresholds (benchmark mode)
    use_regime=True   →  rule-based regime classification per day (no AI calls)
    """
    from services.chartedge_core.config import load_config
    from services.chartedge_core.indstocks import IndstocksMarketRuntime

    config = load_config()
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)

    # ── Always disable AI signal review ──────────────────────────────────────
    runtime.signal_engine.ai_enabled = False

    # ── Patch regime agent to use rule-based only (skip LLM calls) ───────────
    # We monkey-patch AIRegimeAgent.determine_threshold so it always falls
    # back to the deterministic rule-based classifier without making any API call.
    if use_regime:
        from services.chartedge_core import regime_agent as _ra
        from services.chartedge_core.regime_agent import _classify_rule_based

        class _RuleOnlyAgent:
            """Drop-in replacement — deterministic, no LLM calls."""
            async def determine_threshold(
                self, symbol, target_date, prev_day_candles, vix_price,
                current_open=None, global_context=None
            ):
                result = _classify_rule_based(prev_day_candles, vix_price)
                regime = result["market_regime"]
                threshold = result["confluence_threshold"]
                print(
                    f"  📐 [RuleRegime] {symbol} → {regime} | "
                    f"threshold={threshold:.2f} | VIX={vix_price:.1f}"
                )
                return result

        # Inject into the runtime so run_backtest uses it
        original_cls = _ra.AIRegimeAgent
        _ra.AIRegimeAgent = _RuleOnlyAgent

    mode_label = "RULE-BASED REGIME" if use_regime else "NO REGIME (benchmark)"
    print(f"\n>>> [{mode_label}] {start.date()} → {end.date()}")
    result = await runtime.run_backtest(
        start, end, run_regime_agent=use_regime
    )
    if result.get("status") != "ok":
        print(f"ERROR: {result}")
        return {"error": str(result)}

    if use_regime:
        # Restore original class
        _ra.AIRegimeAgent = original_cls  # noqa

    return _summarise(runtime)


# ── main ──────────────────────────────────────────────────────────────────────

async def main():
    months = [
        ("April 2026",  datetime(2026, 4,  1, tzinfo=IST), datetime(2026, 4, 30, 15, 30, tzinfo=IST)),
        ("May 2026",    datetime(2026, 5,  1, tzinfo=IST), datetime(2026, 5, 31, 15, 30, tzinfo=IST)),
        ("June 2026",   datetime(2026, 6,  1, tzinfo=IST), datetime(2026, 6, 15, 15, 30, tzinfo=IST)),
    ]

    print("\n" + "="*70)
    print("  BENCHMARK vs RULE-BASED REGIME — Apr-Jun 2026")
    print("="*70)

    no_regime_results   = {}
    rule_regime_results = {}

    for label, start, end in months:
        # Run A — no regime
        res_a = await run_month(start, end, use_regime=False)
        no_regime_results[label] = res_a

        # Run B — rule-based regime
        res_b = await run_month(start, end, use_regime=True)
        rule_regime_results[label] = res_b

    # ── Print comparison table ─────────────────────────────────────────────
    print("\n" + "="*70)
    print(f"{'Month':15} | {'--- NO Regime (benchmark) ---':35} | {'--- Rule-Based Regime ---':35}")
    print(f"{'':15} | {'Opts':>8} {'Futs':>10} {'Combined':>12} {'WR%':>5} | {'Opts':>8} {'Futs':>10} {'Combined':>12} {'WR%':>5}")
    print("-"*70)

    tot_a_opt = tot_a_fut = tot_a_com = 0
    tot_b_opt = tot_b_fut = tot_b_com = 0

    for label, _, _ in months:
        a = no_regime_results[label]
        b = rule_regime_results[label]
        if "error" in a or "error" in b:
            print(f"{label:15} | ERROR")
            continue
        print(
            f"{label:15} | "
            f"{a['options']:>+8,.0f} {a['futures']:>+10,.0f} {a['combined']:>+12,.0f} {a['win_pct']:>5.1f} | "
            f"{b['options']:>+8,.0f} {b['futures']:>+10,.0f} {b['combined']:>+12,.0f} {b['win_pct']:>5.1f}"
        )
        tot_a_opt += a.get("options", 0)
        tot_a_fut += a.get("futures", 0)
        tot_a_com += a.get("combined", 0)
        tot_b_opt += b.get("options", 0)
        tot_b_fut += b.get("futures", 0)
        tot_b_com += b.get("combined", 0)

    print("-"*70)
    print(
        f"{'TOTAL':15} | "
        f"{tot_a_opt:>+8,.0f} {tot_a_fut:>+10,.0f} {tot_a_com:>+12,.0f} {'':>5} | "
        f"{tot_b_opt:>+8,.0f} {tot_b_fut:>+10,.0f} {tot_b_com:>+12,.0f} {'':>5}"
    )
    print("="*70)

    diff = tot_b_com - tot_a_com
    if diff > 0:
        print(f"\n✅ Rule-based regime is BETTER by ₹{diff:+,.0f}")
    elif diff < 0:
        print(f"\n🔴 Rule-based regime is WORSE by ₹{abs(diff):,.0f} — keep static thresholds")
    else:
        print(f"\n⚪ No difference — regime agent has no effect")

    print(f"\n📌 Locked benchmark: ₹+69,929.05")
    print(f"   No-Regime total:   ₹{tot_a_com:+,.2f}  (should match benchmark)")
    print(f"   Rule-Regime total: ₹{tot_b_com:+,.2f}")


if __name__ == "__main__":
    asyncio.run(main())

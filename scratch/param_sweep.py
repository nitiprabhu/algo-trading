import asyncio
import os
import sys
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo
from tabulate import tabulate
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")
os.environ["SKIP_DB_OVERRIDES"] = "1"

CONFIGS = [
    {
        "name": "Config A: Conservative (ME=ON, Lots=3, ADX=30)",
        "overrides": {"mutual_exclusion": True, "max_lots": 3, "adx_min_trend": 30.0, "confidence_floor": 85}
    },
    {
        "name": "Config B: Moderate (ME=ON, Lots=3, ADX=25)",
        "overrides": {"mutual_exclusion": True, "max_lots": 3, "adx_min_trend": 25.0, "confidence_floor": 85}
    },
    {
        "name": "Config C: Aggressive (ME=OFF, Lots=2, ADX=30)",
        "overrides": {"mutual_exclusion": False, "max_lots": 2, "adx_min_trend": 30.0, "confidence_floor": 85}
    },
    {
        "name": "Config D: Full Power (ME=OFF, Lots=3, ADX=30)",
        "overrides": {"mutual_exclusion": False, "max_lots": 3, "adx_min_trend": 30.0, "confidence_floor": 85}
    }
]

async def run_quarter_for_config(overrides):
    config = load_config()
    
    # Apply overrides
    config.risk["total_capital"] = 500000.0
    config.risk["notional_per_trade"] = 500000.0
    config.risk["mutual_exclusion"] = overrides["mutual_exclusion"]
    config.risk["adx_min_trend"] = overrides["adx_min_trend"]
    config.risk["confidence_floor"] = overrides["confidence_floor"]
    config.futures_risk["NIFTY_FUT"]["max_lots"] = overrides["max_lots"]

    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.signal_engine.ai_enabled = False

    start = datetime(2026, 4, 1, tzinfo=IST)
    end = datetime(2026, 6, 15, 15, 30, tzinfo=IST)

    # Suppress print statements during backtest
    sys.stdout = open(os.devnull, 'w')
    result = await runtime.run_backtest(start, end, run_regime_agent=True)
    sys.stdout = sys.__stdout__

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
        "Options PnL": round(opt_pnl, 2),
        "Futures PnL": round(fut_pnl, 2),
        "Combined PnL": round(combined, 2),
        "Trades": trades,
        "Win Rate (%)": win_pct
    }

async def main():
    print("🚀 Starting Parameter Sweep (Apr 1 - Jun 15) with 500k Capital...")
    results_table = []
    
    for cfg in CONFIGS:
        print(f"Testing {cfg['name']}...")
        res = await run_quarter_for_config(cfg['overrides'])
        row = [cfg['name'], res['Combined PnL'], res['Options PnL'], res['Futures PnL'], res['Trades'], res['Win Rate (%)']]
        results_table.append(row)
        
    headers = ["Config Name", "Net PnL", "Options PnL", "Futures PnL", "Trades", "Win %"]
    print("\n" + "="*80)
    print("📊 PARAMETER SWEEP RESULTS")
    print("="*80)
    print(tabulate(results_table, headers=headers, tablefmt="grid"))
    print("================================================================================")

    # Dump to artifact
    with open("sweep_results.md", "w") as f:
        f.write("# Backtest Parameter Sweep (Capital: ₹500,000)\n\n")
        f.write(tabulate(results_table, headers=headers, tablefmt="github"))

if __name__ == "__main__":
    asyncio.run(main())

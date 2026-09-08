#!/usr/bin/env python3
"""
Standalone execution script for Positional Stocks EOD scan.
This script is designed to run once per day (e.g., via GitHub Actions Cron or DO Function trigger)
at ~15:15 IST (09:45 UTC). It bypasses the in-memory once-per-day guard.

Usage:
  python scripts/run_positional_eod.py
"""
import os
import sys
import asyncio
from pathlib import Path

# Add project root to PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.chartedge_core.config import load_config
from services.chartedge_core.database import create_db_and_tables
from services.chartedge_core.positional_stocks import PositionalStocksEngine
from services.chartedge_core.positional_stocks_runtime import PositionalStocksRuntime


async def main():
    print("Loading config...")
    config = load_config("shared/config.yaml")

    # DB is needed for Trade records
    print("Initializing Database...")
    create_db_and_tables()
    
    config_dict = config.model_dump()
    _positional_stocks_cfg = config_dict.get("positional_stocks_risk", {})
    if not _positional_stocks_cfg.get("enabled", False):
        print("Positional Stocks are disabled in config. Exiting.")
        return
        
    _midcap_cfg = config_dict.get("positional_stocks_midcap_risk", {})
    _smallcap_cfg = config_dict.get("positional_stocks_smallcap_risk", {})

    print("Initializing Engines...")
    engine_main = PositionalStocksEngine(
        capital=_positional_stocks_cfg.get("capital", 100000.0),
        max_positions=_positional_stocks_cfg.get("max_positions", 5),
        stop_loss_pct=_positional_stocks_cfg.get("stop_loss_pct", 4.0),
        target_pct=_positional_stocks_cfg.get("target_pct", 14.0),
        reentry_cooldown_sessions=_positional_stocks_cfg.get("reentry_cooldown_sessions", 0),
        partial_exit_frac=_positional_stocks_cfg.get("partial_exit_frac", 0.5),
        enable_runner_phase=_positional_stocks_cfg.get("enable_runner_phase", True),
    )
    runtime_main = PositionalStocksRuntime(engine_main, _positional_stocks_cfg)
    
    engine_midcap = PositionalStocksEngine(
        capital=_midcap_cfg.get("capital", 100000.0),
        max_positions=_midcap_cfg.get("max_positions", 8),
        stop_loss_pct=_midcap_cfg.get("stop_loss_pct", 4.0),
        target_pct=_midcap_cfg.get("target_pct", 14.0),
        pool="midcap",
        confidence_sizing=_midcap_cfg.get("confidence_sizing", False),
        reentry_cooldown_sessions=_midcap_cfg.get("reentry_cooldown_sessions", 0),
        partial_exit_frac=_midcap_cfg.get("partial_exit_frac", 0.5),
        enable_runner_phase=_midcap_cfg.get("enable_runner_phase", True),
    )
    runtime_midcap = PositionalStocksRuntime(engine_midcap, _midcap_cfg)
    
    engine_smallcap = PositionalStocksEngine(
        capital=_smallcap_cfg.get("capital", 100000.0),
        max_positions=_smallcap_cfg.get("max_positions", 10),
        stop_loss_pct=_smallcap_cfg.get("stop_loss_pct", 4.0),
        target_pct=_smallcap_cfg.get("target_pct", 14.0),
        pool="smallcap",
        confidence_sizing=_smallcap_cfg.get("confidence_sizing", False),
        reentry_cooldown_sessions=_smallcap_cfg.get("reentry_cooldown_sessions", 0),
        partial_exit_frac=_smallcap_cfg.get("partial_exit_frac", 0.5),
        enable_runner_phase=_smallcap_cfg.get("enable_runner_phase", True),
    )
    runtime_smallcap = PositionalStocksRuntime(engine_smallcap, _smallcap_cfg)

    print("Running check_once_per_day(force=True) for Nifty500...")
    res1 = await runtime_main.check_once_per_day(force=True)
    print(f"Main result: {res1}")
    
    print("Running check_once_per_day(force=True) for Midcap...")
    res2 = await runtime_midcap.check_once_per_day(force=True)
    print(f"Midcap result: {res2}")
    
    print("Running check_once_per_day(force=True) for Smallcap...")
    res3 = await runtime_smallcap.check_once_per_day(force=True)
    print(f"Smallcap result: {res3}")

    print("Done. EOD scan completed.")

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")
DRAWDOWN_DATES = ["2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08"]

async def main():
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)
    # Enable AI Single review so we can capture the actual veto decisions
    config.ai["enabled"] = True
    config.ai["debate_enabled"] = False
    
    # Force RELIANCE and HDFCBANK to monitor role only
    for inst in config.instruments:
        if inst["symbol"] in ("RELIANCE", "HDFCBANK"):
            inst["role"] = "monitor"
            
    print("=" * 80)
    print("🔍 RUNNING DETAILED DRAWDOWN ANALYSIS FOR MAY 5 - MAY 8, 2026 🔍")
    print("=" * 80)
    
    for date_str in DRAWDOWN_DATES:
        print(f"\n📅 --- {date_str} ANALYSIS ---")
        runtime = IndstocksMarketRuntime(config, skip_db_load=True)
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
        start = datetime.combine(target, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
        end   = datetime.combine(target, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)
        
        # We run the simulation and let stdout print the breakouts and AI responses
        await runtime.run_backtest(start, end)
        
    print("\n" + "=" * 80)
    print("🏁 ANALYSIS RUN COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())

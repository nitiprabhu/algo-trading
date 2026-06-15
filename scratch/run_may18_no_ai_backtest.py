import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

# Load environment variables
load_dotenv()

IST = ZoneInfo("Asia/Kolkata")
DATE_STR = "2026-05-18"

async def main():
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)

    # Initialize runtime
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    
    # FORCE AI DISABLED
    runtime.signal_engine.ai_enabled = False
    
    target = datetime.strptime(DATE_STR, "%Y-%m-%d").date()
    start = datetime.combine(target, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
    end   = datetime.combine(target, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)

    print(f"\n{'='*70}")
    print(f"🚀 INITIALIZING MAY 18, 2026 BACKTEST (NO AI)")
    print(f"{'='*70}")
    print(f"⏰ Range: {start} to {end}")
    print(f"📊 Timeframe: {config.risk.get('timeframe_mins')}m")
    print(f"🤖 AI Signal Review: {'ENABLED' if runtime.signal_engine.ai_enabled else 'DISABLED'}")
    print(f"{'='*70}\n")

    results = await runtime.run_backtest(start, end)

    if results.get("status") == "error":
        print(f"⚠️ Error running backtest: {results.get('reason')}")
        if 'http_status' in results:
            print(f"   HTTP Status: {results.get('http_status')}")
            print(f"   Body: {results.get('body')}")
        return

    total_candles = sum(v for k, v in results.items() if k != "status")
    print(f"\n✅ Data Fetch Successful!")
    for k, v in results.items():
        if k != "status":
            print(f"  - {k}: {v} candles")

    trades = runtime.trader.closed_trades
    m = runtime.trader.metrics()

    print(f"\n{'='*70}")
    print(f"📊 PERFORMANCE METRICS FOR {DATE_STR} (NO AI)")
    print(f"{'='*70}")
    print(f"Total Executed Trades: {len(trades)}")
    print(f"Realized PnL:          ₹{m['realized_pnl']:.2f}")
    print(f"Win Rate:              {m['win_rate']:.1f}%")
    print(f"Profit Factor:         {m.get('profit_factor', 0.0)}")
    print(f"{'='*70}\n")

    # Detailed trade list
    if trades:
        print("📝 DETAILED TRADE LIST:")
        print("-" * 110)
        print(f"{'Instrument':18s} | {'Type':5s} | {'Qty':4s} | {'Entry Px':10s} | {'Exit Px':10s} | {'PnL (₹)':12s} | {'PnL %':8s} | {'Reason':20s}")
        print("-" * 110)
        for t in trades:
            pnl_str = f"₹{t.pnl:.2f}"
            pnl_pct_str = f"{t.pnl_pct:.2f}%"
            print(f"{t.instrument:18s} | {t.direction.value:5s} | {t.quantity:<4d} | {t.entry_price:<10.2f} | {t.exit_price:<10.2f} | {pnl_str:12s} | {pnl_pct_str:8s} | {t.exit_reason:20s}")
        print("-" * 110)
    else:
        print("📭 No trades were executed during this day.")

if __name__ == "__main__":
    asyncio.run(main())

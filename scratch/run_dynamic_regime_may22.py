import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

load_dotenv()

IST = ZoneInfo("Asia/Kolkata")
DATE_STR = "2026-05-22"

async def main():
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)

    # Initialize runtime
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    
    # We will test both rule-based and AI-reviewed signals with dynamic threshold
    runtime.signal_engine.ai_enabled = True # let's run with AI review enabled!
    
    target = datetime.strptime(DATE_STR, "%Y-%m-%d").date()
    start = datetime.combine(target, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
    end   = datetime.combine(target, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)

    print(f"\n🚀 RUNNING BACKTEST FOR {DATE_STR} WITH DYNAMIC REGIME AGENT")
    results = await runtime.run_backtest(start, end, run_regime_agent=True)

    if results.get("status") == "error":
        print(f"⚠️ Error running backtest: {results.get('reason')}")
        return

    trades = runtime.trader.closed_trades
    m = runtime.trader.metrics()

    print(f"\n📊 PERFORMANCE METRICS FOR {DATE_STR} (DYNAMIC THRESHOLD + AI REVIEW)")
    print("-" * 50)
    print(f"Chosen Confluence Threshold: {runtime.signal_engine.thresholds.get('DEFAULT')}")
    print(f"Total Executed Trades:       {len(trades)}")
    print(f"Realized PnL:                ₹{m['realized_pnl']:.2f}")
    print(f"Win Rate:                    {m['win_rate']:.1f}%")
    print(f"Profit Factor:               {m.get('profit_factor', 0.0)}")
    print("-" * 50)

    if trades:
        print("📝 DETAILED TRADE LIST:")
        for t in trades:
            exit_pr = t.exit_price if t.exit_price else 0.0
            print(f"Trade: {t.instrument} | {t.direction.value} | Qty {t.quantity} | Entry: {t.entry_time.strftime('%H:%M')} ({t.entry_price:.2f}) | Exit: {t.exit_time.strftime('%H:%M') if t.exit_time else 'N/A'} ({exit_pr:.2f}) | PnL: {t.pnl:+.2f} ({t.pnl_pct:+.2f}%) | {t.exit_reason}")

if __name__ == "__main__":
    asyncio.run(main())

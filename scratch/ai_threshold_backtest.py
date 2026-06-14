import asyncio
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from tabulate import tabulate
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")

async def run_backtest_variant(ai_enabled, threshold_val, start_date, end_date):
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)
    
    # Set the confluence threshold
    for key in config.confluence_thresholds:
        config.confluence_thresholds[key] = threshold_val
    config.confluence_thresholds["DEFAULT"] = threshold_val
    
    # Risk settings
    config.risk["confidence_floor"] = 60
    
    # AI settings
    config.ai["enabled"] = ai_enabled
    config.ai["debate_enabled"] = False # Ensure single AI review
    
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.trader.is_backtesting = True
    
    # Patch _fetch_historical to fetch in 5-day chunks
    original_fetch = runtime._fetch_historical
    def chunked_fetch(token, instrument, s_date, e_date):
        from datetime import timedelta
        all_candles = []
        current = s_date
        while current < e_date:
            nxt = min(current + timedelta(days=5), e_date)
            chunk = original_fetch(token, instrument, current, nxt)
            if chunk:
                all_candles.extend(chunk)
            current = nxt
        return all_candles
    
    runtime._fetch_historical = chunked_fetch
    
    print(f"🔄 Running {'AI REVIEW' if ai_enabled else 'RULE-BASED'} variant (Threshold: {threshold_val})...")
    results = await runtime.run_backtest(start_date, end_date)
    
    if results.get("status") == "error":
        print(f"❌ Error in {threshold_val} variant:")
        print(results)
        # Return empty metrics to avoid unpacking error, but keep trades empty
        return [], {"realized_pnl": 0, "win_rate": 0, "profit_factor": 0}
        
    return runtime.trader.closed_trades, runtime.trader.metrics()

async def main():
    start = datetime(2026, 4, 1, 9, 15, tzinfo=IST)
    end = datetime(2026, 4, 30, 15, 30, tzinfo=IST)
    threshold = 0.50
    
    print(f"🚀 AI REVIEW BACKTEST COMPARISON")
    print(f"📅 Period: {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
    print(f"🔢 Testing Confluence Threshold: {threshold}")
    print("-" * 50)

    # Variant 1: Rule-Based Only
    rb_trades, rb_metrics = await run_backtest_variant(False, threshold, start, end)
    
    # Variant 2: AI Review
    ai_trades, ai_metrics = await run_backtest_variant(True, threshold, start, end)
    
    if not rb_trades and not ai_trades:
        print("❌ Both backtests failed to produce results. Please check your INDSTOCKS_TOKEN.")
        return

    print("\n" + "=" * 80)
    print("📊 AI REVIEW PERFORMANCE COMPARISON (APRIL 2026 FULL)")
    print("=" * 80)
    
    headers = ["Strategy", "Total Trades", "Net PnL (₹)", "Win Rate (%)", "Profit Factor"]
    table_data = [
        ["Rule-Based Only", len(rb_trades), f"{rb_metrics['realized_pnl']:+,.2f}", f"{rb_metrics['win_rate']:.1f}%", rb_metrics["profit_factor"]],
        ["Single AI Review", len(ai_trades), f"{ai_metrics['realized_pnl']:+,.2f}", f"{ai_metrics['win_rate']:.1f}%", ai_metrics["profit_factor"]]
    ]
        
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    
    # Calculate Impact
    trades_filtered = len(rb_trades) - len(ai_trades)
    pnl_diff = ai_metrics['realized_pnl'] - rb_metrics['realized_pnl']
    
    print("\n📝 AI IMPACT ANALYSIS:")
    print(f"📉 Trades Filtered by AI: {trades_filtered}")
    if pnl_diff > 0:
        print(f"✅ AI added ₹{pnl_diff:,.2f} in value by filtering bad trades or confirming better ones.")
    elif pnl_diff < 0:
        print(f"⚠️ AI reduced profit by ₹{abs(pnl_diff):,.2f} (likely by filtering some winning trades).")
    else:
        print(f"⚖️ AI and Rule-Based performance was identical.")

if __name__ == "__main__":
    asyncio.run(main())

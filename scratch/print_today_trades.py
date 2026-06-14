import asyncio
import os
import yaml
from datetime import datetime
from zoneinfo import ZoneInfo
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")

async def main():
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)
    
    # Set threshold to 0.50
    for key in config.confluence_thresholds:
        config.confluence_thresholds[key] = 0.50
    config.confluence_thresholds["DEFAULT"] = 0.50
    
    config.risk["confidence_floor"] = 60
    config.ai["enabled"] = False
    
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.trader.is_backtesting = True
    
    start = datetime(2026, 5, 22, 9, 15, tzinfo=IST)
    end = datetime(2026, 5, 22, 15, 30, tzinfo=IST)
    
    results = await runtime.run_backtest(start, end)
    
    print("\n" + "=" * 80)
    print("📋 TRADES TAKEN ON MAY 22, 2026 WITH CONFLUENCE THRESHOLD = 0.50")
    print("=" * 80)
    
    closed_trades = runtime.trader.closed_trades
    for i, trade in enumerate(closed_trades):
        # Let's find the matching signal if we can, or just print what we have
        # Since signals list is on runtime.signals, let's look for signal matching signal_id
        matching_sig = next((s for s in runtime.signals if s.id == trade.signal_id), None)
        strategy = matching_sig.strategy_name if matching_sig else "CONFLUENCE"
        option_type = matching_sig.option_type if matching_sig else "N/A"
        
        print(f"Trade #{i+1}:")
        print(f"  Instrument:   {trade.instrument} (Strategy: {strategy}, Type: {option_type})")
        print(f"  Direction:    {trade.direction.value}")
        print(f"  Entry:        {trade.entry_time.strftime('%H:%M')} at ₹{trade.entry_price:.2f}")
        exit_px_str = f"₹{trade.exit_price:.2f}" if trade.exit_price is not None else "N/A"
        print(f"  Exit:         {trade.exit_time.strftime('%H:%M') if trade.exit_time else 'N/A'} at {exit_px_str}")
        print(f"  Exit Reason:  {trade.exit_reason}")
        print(f"  Invested:     ₹{trade.invested_amount:.2f}")
        print(f"  PnL:          ₹{trade.pnl:+.2f} ({trade.pnl_pct:+.2f}%)")
        print("-" * 50)

    metrics = runtime.trader.metrics()
    print("\n=== Summary Metrics ===")
    print(f"Net PnL:        ₹{metrics['realized_pnl']:+,.2f}")
    print(f"Win Rate:       {metrics['win_rate']:.1f}%")
    print(f"Profit Factor:  {metrics['profit_factor']:.2f}")

if __name__ == "__main__":
    asyncio.run(main())

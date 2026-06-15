import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

# Always INDmoney — never Zerodha cache
os.environ.pop("ZERODHA_CACHE_DIR", None)

IST = ZoneInfo("Asia/Kolkata")

async def main():
    from services.chartedge_core.config import load_config
    from services.chartedge_core.indstocks import IndstocksMarketRuntime
    
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)
    
    # Initialize runtime
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.signal_engine.ai_enabled = False
    
    # Target June 1 to June 15, 2026
    start = datetime(2026, 6, 1, tzinfo=IST)
    end = datetime(2026, 6, 15, 15, 30, tzinfo=IST)
    
    print(f"🚀 Starting full June 2026 Backtest ({start.date()} to {end.date()})")
    
    results = await runtime.run_backtest(start, end, run_regime_agent=True)
    
    print("\n" + "="*80)
    print("🏁 BACKTEST RESULTS FOR FULL JUNE 2026")
    print("="*80)
    
    opt_pnl = sum(t.pnl for t in runtime.trader.closed_trades)
    fut_pnl = sum(t.pnl for t in runtime.futures_trader.closed_trades)
    combined = opt_pnl + fut_pnl
    
    opt_trades = len(runtime.trader.closed_trades)
    fut_trades = len(runtime.futures_trader.closed_trades)
    
    print(f"Options PnL:  ₹{opt_pnl:.2f} ({opt_trades} trades)")
    print(f"Futures PnL:  ₹{fut_pnl:.2f} ({fut_trades} trades)")
    print(f"Combined PnL: ₹{combined:.2f}")
    
    # Details of options trades
    if runtime.trader.closed_trades:
        print("\n📝 OPTIONS TRADES SUMMARY:")
        for t in runtime.trader.closed_trades[:15]:
            print(f"  - {t.instrument} ({t.direction.value}): Entry={t.entry_price} Exit={t.exit_price} PnL=₹{t.pnl:.2f} Reason={t.exit_reason}")
        if len(runtime.trader.closed_trades) > 15:
            print(f"  ... and {len(runtime.trader.closed_trades) - 15} more trades")
            
    # Details of futures trades
    if runtime.futures_trader.closed_trades:
        print("\n📝 FUTURES TRADES SUMMARY:")
        for t in runtime.futures_trader.closed_trades:
            print(f"  - {t.instrument} ({t.direction.value}): Entry={t.entry_price} Exit={t.exit_price} PnL=₹{t.pnl:.2f} Reason={t.exit_reason}")
    print("="*80)

    # Format Telegram message
    msg = (
        "📊 *Full June 2026 Backtest Report*\n"
        "_(Regime Agent: ON | AI Review: OFF)_\n\n"
        f"  - Options PnL: `₹{opt_pnl:+,.2f}` (`{opt_trades}` trades)\n"
        f"  - Futures PnL: `₹{fut_pnl:+,.2f}` (`{fut_trades}` trades)\n"
        f"  - Combined PnL: `₹{combined:+,.2f}`\n\n"
    )
            
    from services.chartedge_core.telegram import notifier
    await notifier.send_message(msg)
    print("📢 Notification sent to Telegram successfully!")

if __name__ == "__main__":
    asyncio.run(main())

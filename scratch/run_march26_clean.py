import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
IST = ZoneInfo("Asia/Kolkata")

async def main():
    from services.chartedge_core.config import load_config
    from services.chartedge_core.indstocks import IndstocksMarketRuntime

    config = load_config()
    # Ensure RELIANCE and HDFCBANK are role: monitor (production default)
    for inst in config.instruments:
        if inst["symbol"] in ("RELIANCE", "HDFCBANK"):
            inst["role"] = "monitor"
    if "RELIANCE" in config.trading_symbols:
        config.trading_symbols.remove("RELIANCE")
    if "HDFCBANK" in config.trading_symbols:
        config.trading_symbols.remove("HDFCBANK")

    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    runtime.signal_engine.ai_enabled = False

    start_date = datetime(2026, 3, 1, tzinfo=IST)
    end_date = datetime(2026, 3, 31, 15, 30, tzinfo=IST)

    print(f"🔄 Running clean March 2026 backtest (Options & Futures only)...")
    result = await runtime.run_backtest(start_date, end_date, run_regime_agent=True)
    
    if result.get("status") != "ok":
        print(f"ERROR: Backtest failed. Result: {result}")
        return

    closed = runtime.trader.closed_trades + [
        t.to_paper_trade() for t in runtime.futures_trader.closed_trades
    ]
    
    opt_pnl = sum(t.pnl for t in runtime.trader.closed_trades)
    fut_pnl = sum(t.pnl for t in runtime.futures_trader.closed_trades)
    combined = opt_pnl + fut_pnl
    trades = len(closed)
    wins = sum(1 for t in closed if t.pnl > 0)
    win_pct = round(wins / trades * 100, 1) if trades else 0.0

    print("\n" + "="*50)
    print("🏆 MARCH 2026 CLEAN BACKTEST RESULTS:")
    print("="*50)
    print(f"Combined PnL: ₹{combined:+,.2f} (Opts: ₹{opt_pnl:+,.2f}, Futs: ₹{fut_pnl:+,.2f})")
    print(f"Total Trades: {trades} | Win Rate: {win_pct}%")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(main())

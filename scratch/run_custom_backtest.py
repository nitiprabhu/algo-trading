import asyncio
import os

# Strict YAML configuration usage
os.environ["SKIP_DB_OVERRIDES"] = "1"

from datetime import datetime
from zoneinfo import ZoneInfo

from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")

# May 2026 trading days
DATES = [
    "2026-05-01", "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07",
    "2026-05-08", "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14",
    "2026-05-15", "2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21",
    "2026-05-22", "2026-05-25", "2026-05-26", "2026-05-27", "2026-05-28",
    "2026-05-29"
]

async def run_date(date_str: str, enable_equity: bool = False):
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)

    # Temporarily promote RELIANCE and HDFCBANK to trading role
    if enable_equity:
        for inst in config.instruments:
            if inst["symbol"] in ("RELIANCE", "HDFCBANK"):
                inst["role"] = "trading"
        # Also add them to trading_symbols
        for sym in ("RELIANCE", "HDFCBANK"):
            if sym not in config.trading_symbols:
                config.trading_symbols.append(sym)

    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    target = datetime.strptime(date_str, "%Y-%m-%d").date()
    start = datetime.combine(target, datetime.strptime("09:00", "%H:%M").time(), tzinfo=IST)
    end   = datetime.combine(target, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)

    print(f"\n🔄 Processing Backtest for {date_str}...")
    results = await runtime.run_backtest(start, end)

    if results.get("status") == "error":
        print(f"⚠️ Error running backtest for {date_str}: {results.get('reason')}")
        if 'http_status' in results:
            print(f"   HTTP Status: {results.get('http_status')}")
            print(f"   Body: {results.get('body')}")
        return None

    total_candles = sum(v for k, v in results.items() if k != "status")
    if total_candles == 0:
        return {"holiday": True, "date": date_str}

    trades = runtime.trader.closed_trades
    m = runtime.trader.metrics()

    # Log summary of day's execution
    print(f"  ✅ {date_str}: {len(trades)} Trades | Net PnL: ₹{m['realized_pnl']:.2f} | Win Rate: {m['win_rate']:.1f}% ({total_candles} candles)")
    
    return {
        "holiday": False,
        "date": date_str,
        "metrics": m,
        "trades": [
            {
                "instrument": t.instrument,
                "direction": t.direction.value,
                "entry": t.entry_price,
                "exit": t.exit_price,
                "qty": t.quantity,
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "reason": t.exit_reason
            }
            for t in trades
        ]
    }

async def main():
    print(f"\n{'='*70}")
    print(f"🚀 INITIALIZING BACKTEST FOR MAY 2026")
    print(f"{'='*70}")
    print(f"📅 Target Window: May 01, 2026 to May 31, 2026")
    print(f"🔧 Testing Instruments: NIFTY, BANKNIFTY, RELIANCE, HDFCBANK")
    print(f"{'='*70}\n")

    daily_results = []
    
    for date_str in DATES:
        day_res = await run_date(date_str)
        if day_res is not None:
            daily_results.append(day_res)

    print(f"\n\n{'='*70}")
    print(f"📊 BACKTEST COMPLETE")
    print(f"{'='*70}\n")

    total_trades = 0
    total_pnl = 0.0
    wins = 0
    losses = 0
    active_days = 0

    print("📅 DAILY PERFORMANCE TABLE:")
    print("-" * 75)
    print(f"{'Date':12s} | {'Status':10s} | {'Trades':8s} | {'Wins/Losses':12s} | {'Net PnL (₹)':12s} | {'Win %':6s}")
    print("-" * 75)

    for res in daily_results:
        date_str = res["date"]
        if res["holiday"]:
            print(f"{date_str:12s} | {'Holiday/WE':10s} | {'-':8s} | {'-':12s} | {'-':12s} | {'-':6s}")
        else:
            active_days += 1
            m = res["metrics"]
            trades_count = len(res["trades"])
            day_wins = sum(1 for t in res["trades"] if t["pnl"] > 0)
            day_losses = sum(1 for t in res["trades"] if t["pnl"] < 0)
            
            total_trades += trades_count
            total_pnl += m["realized_pnl"]
            wins += day_wins
            losses += day_losses
            
            pnl_str = f"₹{m['realized_pnl']:.2f}"
            win_loss_str = f"{day_wins}W / {day_losses}L"
            win_pct_str = f"{m['win_rate']:.1f}%"
            
            print(f"{date_str:12s} | {'Active':10s} | {trades_count:<8d} | {win_loss_str:12s} | {pnl_str:12s} | {win_pct_str:6s}")

    print("-" * 75)

    # Detailed trade analysis by instrument
    instrument_stats = {}
    for res in daily_results:
        if not res["holiday"]:
            for t in res["trades"]:
                sym = t["instrument"]
                if sym not in instrument_stats:
                    instrument_stats[sym] = {"trades": 0, "pnl": 0.0, "wins": 0}
                
                instrument_stats[sym]["trades"] += 1
                instrument_stats[sym]["pnl"] += t["pnl"]
                if t["pnl"] > 0:
                    instrument_stats[sym]["wins"] += 1

    print("\n📦 PERFORMANCE BY INSTRUMENT:")
    print("-" * 60)
    print(f"{'Instrument':18s} | {'Trades':8s} | {'Win %':8s} | {'Realized PnL (₹)':16s}")
    print("-" * 60)
    for sym, stats in instrument_stats.items():
        win_rate = (stats["wins"] / stats["trades"] * 100) if stats["trades"] > 0 else 0.0
        print(f"{sym:18s} | {stats['trades']:<8d} | {win_rate:.1f}%  | ₹{stats['pnl']:<15.2f}")
    print("-" * 60)

    # List of all trades
    print("\n📝 DETAILED TRADE LIST:")
    print("-" * 100)
    print(f"{'Date':10s} | {'Instrument':15s} | {'Type':5s} | {'Qty':4s} | {'Entry':8s} | {'Exit':8s} | {'PnL (₹)':12s} | {'Reason':20s}")
    print("-" * 100)
    for res in daily_results:
        if not res["holiday"]:
            for t in res["trades"]:
                pnl_str = f"₹{t['pnl']:.2f}"
                print(f"{res['date']:10s} | {t['instrument']:15s} | {t['direction']:5s} | {t['qty']:<4d} | {t['entry']:<8.2f} | {t['exit']:<8.2f} | {pnl_str:12s} | {t['reason']:20s}")
    print("-" * 100)

    print(f"\n{'='*70}")
    print(f"🏆 GRAND AGGREGATE SUMMARY")
    print(f"{'='*70}")
    print(f"Active Trading Days:   {active_days} Days")
    print(f"Total Executed Trades: {total_trades} Trades")
    print(f"Winning Trades:        {wins} W")
    print(f"Losing Trades:         {losses} L")
    if total_trades:
        print(f"Overall Win Rate:      {wins / total_trades * 100:.2f}%")
    else:
        print(f"Overall Win Rate:      0.00%")
    print(f"Grand Total Net PnL:   ₹{total_pnl:.2f}")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    asyncio.run(main())

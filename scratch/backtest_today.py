import httpx
import asyncio
import json
from datetime import datetime

async def main():
    target_date = "2026-05-05"
    print(f"🚀 Starting backtest for {target_date}...")
    
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            # 1. Trigger the backtest
            response = await client.post(f"http://127.0.0.1:8000/api/backtest?target_date={target_date}")
            response.raise_for_status()
            data = response.json()
            
            if data.get("backtest", {}).get("status") == "error":
                print(f"❌ Backtest failed: {data['backtest']}")
                return

            # 2. Get the trade log from the snapshot
            snapshot = data.get("snapshot", {})
            trades = snapshot.get("trades", [])
            metrics = snapshot.get("metrics", {})
            
            # 3. Generate a clean report
            print("\n" + "="*50)
            print(f"📊 BACKTEST REPORT: {target_date}")
            print("="*50)
            print(f"💰 Total PnL: {metrics.get('total_pnl', 0):.2f}")
            print(f"📈 Win Rate: {metrics.get('win_rate', 0):.2f}%")
            print(f"🔄 Total Trades: {metrics.get('total_trades', 0)}")
            print(f"📊 Max Drawdown: {metrics.get('max_drawdown', 0):.2f}%")
            print("-" * 50)
            
            if not trades:
                print("No trades were taken today.")
            else:
                print(f"{'Symbol':<15} | {'Side':<5} | {'Entry':<8} | {'Exit':<8} | {'PnL':<8} | {'Reason'}")
                print("-" * 80)
                for trade in trades:
                    symbol = trade.get("symbol", "N/A")
                    side = trade.get("side", "N/A")
                    entry = trade.get("entry_price", 0)
                    exit_p = trade.get("exit_price", 0)
                    pnl = trade.get("pnl", 0)
                    reason = trade.get("exit_reason", "N/A")
                    print(f"{symbol:<15} | {side:<5} | {entry:<8.2f} | {exit_p:<8.2f} | {pnl:<8.2f} | {reason}")
            
            print("="*50)
            
            # Save report to file
            report_file = f"reports/backtest_report_{target_date}.json"
            import os
            os.makedirs("reports", exist_ok=True)
            with open(report_file, "w") as f:
                json.dump(data, f, indent=4)
            print(f"\n✅ Full report saved to {report_file}")

        except Exception as e:
            print(f"❌ Error during backtest execution: {e}")

if __name__ == "__main__":
    asyncio.run(main())

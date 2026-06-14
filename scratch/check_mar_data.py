import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")

async def main():
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)
    
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    token = os.getenv("INDMONEY_TOKEN") or os.getenv("INDSTOCKS_TOKEN")
    
    start = datetime(2026, 3, 1, 9, 15, tzinfo=IST)
    end = datetime(2026, 3, 31, 15, 30, tzinfo=IST)
    
    print(f"Fetching data from {start} to {end}...")
    
    for instrument in config.instruments:
        if not instrument.get("enabled", True):
            continue
        symbol = instrument["symbol"]
        try:
            candles = await asyncio.to_thread(runtime._fetch_historical, token, instrument, start, end)
            if candles:
                first_date = candles[0].time
                last_date = candles[-1].time
                print(f"{symbol}: {len(candles)} candles. From {first_date} to {last_date}")
            else:
                print(f"{symbol}: 0 candles.")
        except Exception as e:
            print(f"{symbol} fetch failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())

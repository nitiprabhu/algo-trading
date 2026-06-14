import asyncio
import os
import yaml
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo
from services.chartedge_core.config import Config
from services.chartedge_core.indstocks import IndstocksMarketRuntime

IST = ZoneInfo("Asia/Kolkata")

async def main():
    with open("shared/config.yaml", "r") as f:
        config_data = yaml.safe_load(f)
    config = Config(**config_data)
    
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    
    # We load today's candles from Indstocks (re-using seed history logic)
    # Let's see what symbols are there
    symbols = ["NIFTY", "BANKNIFTY", "INDIAVIX"]
    
    # Run the backtest runner's loader or just get historical data directly
    start = datetime(2026, 5, 22, 9, 15, tzinfo=IST)
    end = datetime(2026, 5, 22, 15, 30, tzinfo=IST)
    
    print("Fetching NIFTY and BANKNIFTY candles for May 22, 2026...")
    token = os.getenv("INDMONEY_TOKEN") or os.getenv("INDSTOCKS_TOKEN")
    if not token:
        print("Token missing!")
        return

    import httpx
    headers = {"Authorization": token}
    indstocks = config.data["indstocks"]
    url = f"{indstocks['base_url']}/market/historical/{indstocks['historical_interval']}"
    
    for symbol in symbols:
        instrument = next((i for i in config.instruments if i["symbol"] == symbol), None)
        if not instrument:
            continue
        params = {
            "scrip-codes": instrument["historical_scrip_code"],
            "start_time": int(start.timestamp() * 1000),
            "end_time": int(end.timestamp() * 1000),
        }
        resp = httpx.get(url, headers=headers, params=params, timeout=60.0)
        if resp.status_code != 200:
            print(f"Error for {symbol}: {resp.status_code}")
            continue
        
        data = resp.json()
        scrip_code = instrument["historical_scrip_code"]
        raw_data = data.get("data", {})
        if isinstance(raw_data.get("candles"), list):
            raw_candles = raw_data["candles"]
        elif isinstance(raw_data.get(scrip_code), dict) and isinstance(raw_data[scrip_code].get("candles"), list):
            raw_candles = raw_data[scrip_code]["candles"]
        else:
            raw_candles = []
            
        if not raw_candles:
            print(f"No candles fetched for {symbol}")
            continue
            
        closes = []
        highs = []
        lows = []
        for row in raw_candles:
            if isinstance(row, dict):
                closes.append(float(row["c"]))
                highs.append(float(row["h"]))
                lows.append(float(row["l"]))
            else:
                closes.append(float(row[4]))
                highs.append(float(row[2]))
                lows.append(float(row[3]))
                
        open_val = float(raw_candles[0]["o"]) if isinstance(raw_candles[0], dict) else float(raw_candles[0][1])
        close_val = closes[-1]
        high_val = max(highs)
        low_val = min(lows)
        
        change_pct = (close_val - open_val) / open_val * 100
        range_pct = (high_val - low_val) / open_val * 100
        
        print(f"\n=== {symbol} Stats ===")
        print(f"Open: {open_val:.2f}")
        print(f"Close: {close_val:.2f}")
        print(f"High: {high_val:.2f}")
        print(f"Low: {low_val:.2f}")
        print(f"Intraday Return: {change_pct:+.3f}%")
        print(f"High-Low Range: {range_pct:.3f}%")
        print(f"Candles Count: {len(raw_candles)}")

if __name__ == "__main__":
    asyncio.run(main())

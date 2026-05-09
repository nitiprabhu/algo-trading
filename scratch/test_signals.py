import asyncio
import os
import yaml
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from services.chartedge_core.config import Config
from services.chartedge_core.ai_signal import SignalEngine
import httpx

IST = ZoneInfo("Asia/Kolkata")

async def test_strategy():
    with open("shared/config.yaml", "r") as f:
        config_data = yaml.safe_load(f)
    config = Config(**config_data)
    engine = SignalEngine(config.ai, config.confluence_thresholds)
    
    token = os.getenv("INDMONEY_TOKEN") or os.getenv("INDSTOCKS_TOKEN")
    if not token:
        print("Token missing")
        return
    headers = {"Authorization": token}

    # Timestamps
    end = datetime.now(IST)
    start = end - timedelta(days=1)

    # Fetch VIX history
    vix_symbol = "INDIAVIX"
    vix_instrument = next(i for i in config.instruments if i["symbol"] == vix_symbol)
    
    indstocks = config.data["indstocks"]
    vix_url = f"{indstocks['base_url']}/market/historical/{indstocks['historical_interval']}"
    vix_params = {
        "scrip-codes": vix_instrument["historical_scrip_code"],
        "start_time": int(start.timestamp() * 1000),
        "end_time": int(end.timestamp() * 1000),
    }
    
    vix_resp = httpx.get(vix_url, headers=headers, params=vix_params)
    vix_data = vix_resp.json()
    
    # Extract VIX
    scrip_code_vix = vix_instrument["historical_scrip_code"]
    raw_vix_data = vix_data.get("data", {})
    if isinstance(raw_vix_data.get("candles"), list):
        raw_vix_candles = raw_vix_data["candles"]
    elif isinstance(raw_vix_data.get(scrip_code_vix), dict) and isinstance(raw_vix_data[scrip_code_vix].get("candles"), list):
        raw_vix_candles = raw_vix_data[scrip_code_vix]["candles"]
    else:
        raw_vix_candles = []
    
    if raw_vix_candles:
        last_vix = float(raw_vix_candles[-1][4]) if isinstance(raw_vix_candles[-1], list) else float(raw_vix_candles[-1]["c"])
        print(f"Current India VIX: {last_vix}")
        vix_val = last_vix
    else:
        print("VIX data missing, using default 18.0")
        vix_val = 18.0

    # Fetch NIFTY history
    symbol = "NIFTY"
    instrument = next(i for i in config.instruments if i["symbol"] == symbol)
    
    indstocks = config.data["indstocks"]
    url = f"{indstocks['base_url']}/market/historical/{indstocks['historical_interval']}"
    params = {
        "scrip-codes": instrument["historical_scrip_code"],
        "start_time": int(start.timestamp() * 1000),
        "end_time": int(end.timestamp() * 1000),
    }
    headers = {"Authorization": token}
    
    resp = httpx.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        print(f"API Error: {resp.status_code} - {resp.text}")
        return
    data = resp.json()
    
    # Extract candles (using the same logic as indstocks.py)
    scrip_code = instrument["historical_scrip_code"]
    raw_data = data.get("data", {})
    if isinstance(raw_data.get("candles"), list):
        raw_candles = raw_data["candles"]
    elif isinstance(raw_data.get(scrip_code), dict) and isinstance(raw_data[scrip_code].get("candles"), list):
        raw_candles = raw_data[scrip_code]["candles"]
    else:
        raw_candles = []

    from services.chartedge_core.models import Candle
    candles = []
    for row in raw_candles:
        if isinstance(row, dict):
            timestamp = int(row["ts"])
            if timestamp > 10_000_000_000:
                timestamp = timestamp // 1000
            candles.append(Candle(
                time=datetime.fromtimestamp(timestamp, IST),
                instrument=symbol,
                timeframe="1m",
                open=float(row["o"]),
                high=float(row["h"]),
                low=float(row["l"]),
                close=float(row["c"]),
                volume=int(row.get("v", 0)),
            ))
        else:
            candles.append(Candle(
                time=datetime.fromtimestamp(row[0] / 1000, IST),
                instrument=symbol,
                timeframe="1m",
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=int(row[5]),
            ))
    
    print(f"Total candles: {len(candles)}")
    if candles:
        print(f"First candle: {candles[0].time}")
        print(f"Last candle: {candles[-1].time}")
    
    t930_high = 0
    t930_low = 100000
    for c in candles:
        if c.time.time() <= time(9,30):
            t930_high = max(t930_high, c.high)
            t930_low = min(t930_low, c.low)
    
    print(f"9:30 Range: {t930_low} - {t930_high}")
    print(f"Current Price: {candles[-1].close}")
    print(f"Session High: {max(c.high for c in candles)}")
    print(f"Session Low: {min(c.low for c in candles)}")
    
    vix_val = 18.0 # Assume 18 for testing
    
    signals = []
    for i in range(len(candles)):
        # Manual state update for T315 to see logs
        t = candles[i].time.time()
        if time(9,15) <= t <= time(9,30):
            # This is handled inside update()
            pass
        
        sig = await engine.get_fo_signal(candles[i], candles[:i+1], vix_val)
        if sig:
            print(f"SIGNAL: {sig.strategy_name} {sig.signal} at {candles[i].time}")
            signals.append(sig)
            
    if not signals:
        print("No signals found in the last day's data.")

if __name__ == "__main__":
    asyncio.run(test_strategy())

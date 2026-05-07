import httpx
import json

base_url = "http://localhost:8000"

try:
    r = httpx.get(f"{base_url}/api/snapshot")
    snap = r.json()
    print("KEYS in snapshot:", list(snap.keys()))
    
    # Check latest candles times
    if "latest_indicators" in snap:
        print("\n--- Latest Indicators by Instrument ---")
        for sym, ind in snap["latest_indicators"].items():
            print(f"Symbol: {sym}")
            print(f"  Candle Time: {ind.get('candle_time')}")
            print(f"  Price: {ind.get('price')}")
            print(f"  Confluence Score: {ind.get('confluence_score')}")
            print(f"  Indicators: {list(ind.get('indicators', {}).keys())}")
            if "supertrend" in ind.get("indicators", {}):
                print(f"    Supertrend: {ind['indicators']['supertrend']}")
            if "rsi" in ind.get("indicators", {}):
                print(f"    RSI: {ind['indicators']['rsi']}")
                
    if "candles" in snap:
        print("\n--- Candles Count & Latest ---")
        for sym, candles in snap["candles"].items():
            print(f"Symbol: {sym}, count: {len(candles)}")
            if candles:
                print(f"  Latest candle: {candles[-1]}")
                
except Exception as e:
    print("Snapshot failed:", e)

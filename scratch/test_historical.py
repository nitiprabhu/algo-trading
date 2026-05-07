import os
import httpx
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()
token = os.getenv("INDMONEY_TOKEN")
base_url = "https://api.indstocks.com"

def test_historical():
    print(f"Testing Historical API for NIFTY...")
    end = datetime.now()
    start = end - timedelta(days=1)
    
    params = {
        "scrip-codes": "NIDX_40000001",
        "start_time": int(start.timestamp() * 1000),
        "end_time": int(end.timestamp() * 1000),
    }
    
    try:
        r = httpx.get(f"{base_url}/market/historical/1minute", params=params, headers={"Authorization": token}, timeout=15)
        print(f"Status Code: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            candles = data.get("data", {}).get("candles", [])
            if not candles:
                 # Check alternative path
                 candles = data.get("data", {}).get("NIDX_40000001", {}).get("candles", [])
            print(f"Fetched {len(candles)} candles.")
        else:
            print(f"Error: {r.text[:500]}")
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    test_historical()

import httpx
import json

try:
    r = httpx.get("http://localhost:8000/api/snapshot")
    data = r.json()
    nifty = data.get("latest_indicators", {}).get("NIFTY", {})
    print("NIFTY Indicators Keys:", nifty.keys())
    options_data = nifty.get("options_data")
    print("Options Data:", options_data)
except Exception as e:
    print(f"Error: {e}")

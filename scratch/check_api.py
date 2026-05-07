import httpx
import json

base_url = "http://localhost:8000"

print("--- 1. Health Status ---")
try:
    r = httpx.get(f"{base_url}/health")
    print(json.dumps(r.json(), indent=2))
except Exception as e:
    print("Health failed:", e)

print("\n--- 2. Debug Candles ---")
try:
    r = httpx.get(f"{base_url}/api/debug/candles")
    print(json.dumps(r.json(), indent=2))
except Exception as e:
    print("Debug candles failed:", e)

print("\n--- 3. Signals ---")
try:
    r = httpx.get(f"{base_url}/api/signals")
    print(json.dumps(r.json(), indent=2))
except Exception as e:
    print("Signals failed:", e)

print("\n--- 4. Snapshot Signals & Status ---")
try:
    r = httpx.get(f"{base_url}/api/snapshot")
    snap = r.json()
    print("Signal Count in Snapshot:", len(snap.get("signals", [])))
    print("Open Trades Count:", len(snap.get("open_trades", [])))
    print("Closed Trades Count:", len(snap.get("closed_trades", [])))
    print("Latest Signals:", snap.get("signals")[:5] if snap.get("signals") else [])
except Exception as e:
    print("Snapshot failed:", e)

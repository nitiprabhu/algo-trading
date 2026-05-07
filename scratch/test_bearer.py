import os
import httpx
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("INDMONEY_TOKEN")
url = "https://api.indstocks.com/market/historical/1minute?scrip-codes=NIDX_40000001&start_time=1777619035045&end_time=1777964635045"

print(f"Testing WITH Bearer...")
try:
    r = httpx.get(url, headers={"Authorization": f"Bearer {token}"})
    print(f"Status: {r.status_code}")
except Exception as e:
    print(f"Failed: {e}")

print(f"\nTesting WITHOUT Bearer...")
try:
    r = httpx.get(url, headers={"Authorization": token})
    print(f"Status: {r.status_code}")
except Exception as e:
    print(f"Failed: {e}")

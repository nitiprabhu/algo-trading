import os
import httpx
import pandas as pd
import io
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("INDMONEY_TOKEN")
base_url = "https://api.indstocks.com"

def test_fno_master():
    print("Testing F&O Master fetch...")
    headers = {"Authorization": token}
    try:
        r = httpx.get(f"{base_url}/market/instruments", params={"source": "fno"}, headers=headers, timeout=30)
        print(f"Status: {r.status_code}")
        if r.status_code == 200:
            df = pd.read_csv(io.StringIO(r.text))
            print(f"Fetched {len(df)} instruments.")
            print(df.head())
        else:
            print(f"Error: {r.text[:500]}")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    test_fno_master()

import os
import httpx
from dotenv import load_dotenv
import pandas as pd
import io

load_dotenv()

TOKEN = os.getenv("INDMONEY_TOKEN")
BASE_URL = "https://api.indstocks.com"

def find_working_fno_prefix():
    headers = {"Authorization": TOKEN}
    r = httpx.get(f"{BASE_URL}/market/instruments", params={"source": "fno"}, headers=headers, timeout=30)
    df = pd.read_csv(io.StringIO(r.text))
    
    # Try first 5 F&O IDs with different prefixes
    sample_ids = df['SECURITY_ID'].astype(int).head(5).tolist()
    prefixes = ["NFO_", "NSE_", "FUT_", "OPT_", ""]
    
    for prefix in prefixes:
        codes = [f"{prefix}{sid}" for sid in sample_ids]
        print(f"\nTrying prefix: '{prefix}' (Sample: {codes[0]})")
        try:
            r = httpx.get(f"{BASE_URL}/market/quote", params={"scrip-codes": ",".join(codes)}, headers=headers, timeout=10)
            if r.status_code == 200:
                print(f"✅ SUCCESS with prefix '{prefix}'!")
                return
            else:
                print(f"❌ {r.status_code}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    find_working_fno_prefix()

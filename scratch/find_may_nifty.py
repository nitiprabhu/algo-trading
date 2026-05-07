import os
import httpx
import pandas as pd
from dotenv import load_dotenv
import io

load_dotenv()

TOKEN = os.getenv("INDMONEY_TOKEN")
BASE_URL = "https://api.indstocks.com"

def find_current_nifty_options():
    headers = {"Authorization": TOKEN}
    r = httpx.get(f"{BASE_URL}/market/instruments", params={"source": "fno"}, headers=headers, timeout=30)
    df = pd.read_csv(io.StringIO(r.text))
    
    # Filter for NIFTY-May2026 options
    # We'll look for symbols starting with NIFTY-May2026
    mask = (df['TRADING_SYMBOL'].str.contains('NIFTY-May2026', na=False)) & (df['INSTRUMENT_NAME'] == 'OPTIDX')
    may_options = df[mask]
    
    print(f"Found {len(may_options)} May NIFTY options.")
    if not may_options.empty:
        # Sort by strike and show a few
        print(may_options.sort_values('STRIKE_PRICE').head(20)[['SECURITY_ID', 'TRADING_SYMBOL', 'STRIKE_PRICE', 'OPTION_TYPE', 'EXCH']])

if __name__ == "__main__":
    find_current_nifty_options()

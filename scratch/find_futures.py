import os
import httpx
import pandas as pd
from dotenv import load_dotenv
import io

load_dotenv()

TOKEN = os.getenv("INDMONEY_TOKEN")
BASE_URL = "https://api.indstocks.com"

def find_futures():
    headers = {"Authorization": TOKEN}
    print("Fetching F&O instruments for NIFTY FUTURES...")
    response = httpx.get(f"{BASE_URL}/market/instruments", params={"source": "fno"}, headers=headers, timeout=30)
    df = pd.read_csv(io.StringIO(response.text))
    
    # Filter for NIFTY Futures
    # Typically TRADING_SYMBOL is like 'NIFTY24MAYFUT'
    matches = df[
        (df['INSTRUMENT_NAME'] == 'FUTIDX') & 
        (df['SYMBOL_NAME'].str.contains("NIFTY", na=False))
    ]
    
    if not matches.empty:
        # Sort by expiry to get current month
        print(matches[['EXCH', 'SECURITY_ID', 'TRADING_SYMBOL', 'EXPIRY_DATE']].sort_values('EXPIRY_DATE').head(10).to_string())
    else:
        print("No NIFTY Futures found.")

if __name__ == "__main__":
    find_futures()

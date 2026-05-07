import os
import httpx
import pandas as pd
from dotenv import load_dotenv
import io

load_dotenv()

TOKEN = os.getenv("INDMONEY_TOKEN")
BASE_URL = "https://api.indstocks.com"

def find_futures_and_options():
    headers = {"Authorization": TOKEN}
    print("Fetching F&O instruments...")
    r = httpx.get(f"{BASE_URL}/market/instruments", params={"source": "fno"}, headers=headers, timeout=30)
    df = pd.read_csv(io.StringIO(r.text))
    
    # Futures
    print("\n--- NIFTY FUTURES ---")
    fut = df[(df['INSTRUMENT_NAME'] == 'FUTIDX') & (df['SYMBOL_NAME'].str.contains('NIFTY', na=False))]
    print(fut.sort_values('EXPIRY_DATE').head(5)[['SECURITY_ID', 'TRADING_SYMBOL', 'EXPIRY_DATE']])
    
    # Options (ATM strike example)
    print("\n--- NIFTY OPTIONS (Sample ATM) ---")
    opt = df[(df['INSTRUMENT_NAME'] == 'OPTIDX') & (df['SYMBOL_NAME'].str.contains('NIFTY', na=False))]
    # Filter for NIFTY (not NIFTYNXT50 etc)
    opt = opt[opt['TRADING_SYMBOL'].str.startswith('NIFTY-')]
    print(opt.sort_values('EXPIRY_DATE').head(10)[['SECURITY_ID', 'TRADING_SYMBOL', 'STRIKE_PRICE', 'OPTION_TYPE']])

if __name__ == "__main__":
    find_futures_and_options()

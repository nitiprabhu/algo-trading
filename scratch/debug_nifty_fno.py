import os
import httpx
import pandas as pd
from dotenv import load_dotenv
import io

load_dotenv()

TOKEN = os.getenv("INDMONEY_TOKEN")
BASE_URL = "https://api.indstocks.com"

def debug_nifty_fno():
    headers = {"Authorization": TOKEN}
    r = httpx.get(f"{BASE_URL}/market/instruments", params={"source": "fno"}, headers=headers, timeout=30)
    df = pd.read_csv(io.StringIO(r.text))
    
    # Print all unique INSTRUMENT_NAME values
    print("Instrument Names:", df['INSTRUMENT_NAME'].unique())
    
    # Check if 'NIFTY' exists in ANY row
    mask = df.apply(lambda row: row.astype(str).str.contains('NIFTY', case=False).any(), axis=1)
    nifty_df = df[mask]
    print(f"Found {len(nifty_df)} NIFTY-related instruments.")
    
    if not nifty_df.empty:
        print("\nInstrument counts by name:")
        print(nifty_df['INSTRUMENT_NAME'].value_counts())
        
        print("\nSample NIFTY entries:")
        print(nifty_df[['SECURITY_ID', 'TRADING_SYMBOL', 'INSTRUMENT_NAME', 'EXPIRY_DATE']].head(20).to_string())

if __name__ == "__main__":
    debug_nifty_fno()

import os
import httpx
import pandas as pd
from dotenv import load_dotenv
import io

load_dotenv()

TOKEN = os.getenv("INDMONEY_TOKEN")
BASE_URL = "https://api.indstocks.com"

def find_options():
    headers = {"Authorization": TOKEN}
    print("Fetching F&O instruments...")
    response = httpx.get(f"{BASE_URL}/market/instruments", params={"source": "fno"}, headers=headers, timeout=30)
    response.raise_for_status()
    
    df = pd.read_csv(io.StringIO(response.text))
    print(f"Columns: {df.columns.tolist()}")
    print(f"Total symbols: {len(df)}")
    
    # Check all columns for any string
    print("\nSample symbols (first 50):")
    print(df.head(50)[['EXCH', 'SECURITY_ID', 'TRADING_SYMBOL', 'SYMBOL_NAME', 'CUSTOM_SYMBOL']].to_string())

if __name__ == "__main__":
    find_options()

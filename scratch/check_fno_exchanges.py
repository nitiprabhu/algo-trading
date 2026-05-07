import os
import httpx
import pandas as pd
from dotenv import load_dotenv
import io

load_dotenv()

TOKEN = os.getenv("INDMONEY_TOKEN")
BASE_URL = "https://api.indstocks.com"

def check_fno_exchanges():
    headers = {"Authorization": TOKEN}
    r = httpx.get(f"{BASE_URL}/market/instruments", params={"source": "fno"}, headers=headers, timeout=30)
    df = pd.read_csv(io.StringIO(r.text))
    print("Unique Exchanges in F&O list:", df['EXCH'].unique())
    # Find one NIFTY option and print its full row
    nifty = df[df['SYMBOL_NAME'].str.contains('NIFTY', na=False)].head(1)
    print("\nSample NIFTY F&O Row:")
    print(nifty.to_string())

if __name__ == "__main__":
    check_fno_exchanges()

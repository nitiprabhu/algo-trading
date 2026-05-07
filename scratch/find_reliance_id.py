import os
import httpx
import pandas as pd
from dotenv import load_dotenv
import io

load_dotenv()

TOKEN = os.getenv("INDMONEY_TOKEN")
BASE_URL = "https://api.indstocks.com"

def find_reliance_id():
    headers = {"Authorization": TOKEN}
    r = httpx.get(f"{BASE_URL}/market/instruments", params={"source": "equity"}, headers=headers, timeout=30)
    df = pd.read_csv(io.StringIO(r.text))
    print("Columns:", df.columns.tolist())
    rel = df[df['TRADING_SYMBOL'] == 'RELIANCE']
    print("\nRELIANCE ROW:")
    print(rel.to_string())

if __name__ == "__main__":
    find_reliance_id()

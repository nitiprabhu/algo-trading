import os
import httpx
import pandas as pd
from dotenv import load_dotenv
import io

load_dotenv()

TOKEN = os.getenv("INDMONEY_TOKEN")
BASE_URL = "https://api.indstocks.com"

def find_anything_fno():
    headers = {"Authorization": TOKEN}
    print("Fetching ALL instruments to find F&O...")
    
    # Try different sources
    for source in ['equity', 'fno', 'nfo', 'mcx']:
        print(f"\nTrying source: {source}")
        try:
            r = httpx.get(f"{BASE_URL}/market/instruments", params={"source": source}, headers=headers, timeout=30)
            if r.status_code == 200:
                df = pd.read_csv(io.StringIO(r.text))
                print(f"Found {len(df)} instruments.")
                print("Columns:", df.columns.tolist())
                # Look for NIFTY
                # Try multiple columns
                mask = pd.Series(False, index=df.index)
                for col in df.columns:
                    mask |= df[col].astype(str).str.contains("NIFTY", na=False)
                
                nifty = df[mask]
                if not nifty.empty:
                    print("Sample NIFTY entries:")
                    print(nifty.head(10).to_string())
                else:
                    print("No NIFTY found in this source.")
            else:
                print(f"Error {r.status_code}")
        except Exception as e:
            print(f"Failed: {e}")

if __name__ == "__main__":
    find_anything_fno()

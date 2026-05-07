import os
import httpx
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("INDMONEY_TOKEN")
base_url = "https://api.indstocks.com"

def test_quotes():
    # NIFTY spot tokens from the earlier run
    tokens = "NFO:74157,NFO:74164" # 24100 CE and PE
    print(f"Testing quotes for: {tokens}")
    try:
        r = httpx.get(
            f"{base_url}/market/quotes", 
            params={"tokens": tokens}, 
            headers={"Authorization": token}, 
            timeout=10
        )
        print(f"Status: {r.status_code}")
        if r.status_code == 200:
            import json
            print(json.dumps(r.json(), indent=2))
        else:
            print(f"Error: {r.text}")
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    test_quotes()

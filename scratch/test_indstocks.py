import os
import httpx
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("INDMONEY_TOKEN")
base_url = "https://api.indstocks.com"

def test_api():
    print(f"Testing API with token: {token[:10]}...")
    try:
        # Try fetching F&O instruments (fastest way to check token)
        r = httpx.get(f"{base_url}/market/instruments", params={"source": "fno"}, headers={"Authorization": token}, timeout=10)
        print(f"Status Code: {r.status_code}")
        if r.status_code == 200:
            print("Token is VALID. API responded.")
            # print(r.text[:200])
        else:
            print(f"Token might be INVALID or API error: {r.text[:200]}")
    except Exception as e:
        print(f"Network or request error: {e}")

if __name__ == "__main__":
    test_api()

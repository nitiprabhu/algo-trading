import os
import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("INDMONEY_TOKEN")
BASE_URL = "https://api.indstocks.com"

def test_option_chain():
    headers = {"Authorization": TOKEN}
    
    print("Testing /option-chain-symbols...")
    try:
        response = httpx.get(f"{BASE_URL}/option-chain-symbols", params={"token": "NIDX_40000001"}, headers=headers, timeout=10)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            expiries = data.get("data", {}).get("expiries", [])
            if expiries:
                latest_expiry = expiries[0]
                print(f"Latest Expiry: {latest_expiry}")
                
                print(f"\nTesting /option-chain for {latest_expiry}...")
                # Note: Docs show GET but also show a JSON body. In httpx, GET with body is possible but unusual.
                # Let's try passing as params first, then as JSON if it fails.
                payload = {"token": "NIDX_40000001", "count": "10", "expiry": latest_expiry}
                resp2 = httpx.get(f"{BASE_URL}/option-chain", params=payload, headers=headers, timeout=10)
                print(f"Status: {resp2.status_code}")
                print(f"Response: {resp2.text[:500]}...")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_option_chain()

import os
import httpx
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

token = os.getenv("INDMONEY_TOKEN")
base_url = "https://api.indstocks.com"

def check_indstocks():
    if not token:
        print("Error: INDMONEY_TOKEN not found")
        return

    headers = {"Authorization": token}
    
    # 1. Check Connectivity & Profile
    print("--- Checking Profile ---")
    try:
        r = httpx.get(f"{base_url}/user/profile", headers=headers, timeout=10)
        print(f"Profile Status: {r.status_code}")
        if r.status_code == 200:
            print("Successfully connected to Indstocks")
        else:
            print(f"Error Response: {r.text}")
    except Exception as e:
        print(f"Profile Request Failed: {e}")

    # 2. Check F&O Master Data
    print("\n--- Checking F&O Master ---")
    try:
        # source=fno is what we use in DerivativeManager
        r = httpx.get(f"{base_url}/market/instruments", params={"source": "fno"}, headers=headers, timeout=30)
        print(f"F&O Master Status: {r.status_code}")
        if r.status_code == 200:
            content = r.text[:500]
            print(f"F&O Master Sample: {content}...")
        else:
            print(f"Error Response: {r.text}")
    except Exception as e:
        print(f"F&O Master Request Failed: {e}")

    # 3. Check Current Quote (NIFTY 50)
    print("\n--- Checking NIFTY Quote ---")
    try:
        # NIFTY 50 usually has a specific scrip_code, let's try the common one or search
        # For now, let's try to get a quote for a common symbol if we knew the ID
        # Or just check if the instruments endpoint works for 'equity' too
        r = httpx.get(f"{base_url}/market/quote", params={"scrip_code": "NIFTY"}, headers=headers, timeout=10)
        print(f"Quote Status: {r.status_code}")
        if r.status_code == 200:
            print(f"Quote Data: {r.json()}")
        else:
            # Maybe NIFTY needs a numeric code
            print(f"NIFTY Quote (ID-based search) might be needed. Status: {r.status_code}")
    except Exception as e:
        print(f"Quote Request Failed: {e}")

if __name__ == "__main__":
    check_indstocks()

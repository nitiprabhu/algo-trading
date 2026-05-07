import os
import httpx
from dotenv import load_dotenv
import json

load_dotenv()

TOKEN = os.getenv("INDMONEY_TOKEN")
BASE_URL = "https://api.indstocks.com"

def test_option_quote():
    headers = {"Authorization": TOKEN}
    # Token 57042 was NIFTY-Jun2026-23800-CE
    scrip = "NFO_57042" # F&O instruments usually have NFO_ prefix or similar
    
    # Let's try to get a quote
    url = f"{BASE_URL}/market/quote"
    params = {"scrip-codes": scrip}
    
    response = httpx.get(url, params=params, headers=headers, timeout=10)
    print(f"Status: {response.status_code}")
    print(json.dumps(response.json(), indent=2))

if __name__ == "__main__":
    test_option_quote()

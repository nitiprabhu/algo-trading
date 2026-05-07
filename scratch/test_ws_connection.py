import asyncio
import websockets
import os
import json
from dotenv import load_dotenv

load_dotenv()

async def test_ws():
    token = os.getenv("INDMONEY_TOKEN")
    ws_url = "wss://ws-prices.indstocks.com/api/v1/ws/prices"
    
    headers = {
        "Authorization": f"Bearer {token}" if not token.startswith("Bearer ") else token
    }
    
    print(f"Connecting to {ws_url}...")
    print(f"Headers: {headers}")
    
    try:
        async with websockets.connect(ws_url, additional_headers=headers) as ws:
            print("Successfully connected!")
            # Try to subscribe to NIFTY (scrip 13)
            await ws.send(json.dumps({
                "action": "subscribe",
                "mode": "full",
                "instruments": ["indstocks:13"]
            }))
            print("Subscription sent.")
            async for msg in ws:
                print(f"Received: {msg}")
                break
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_ws())

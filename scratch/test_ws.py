import asyncio
import websockets
import json
import os
from dotenv import load_dotenv

async def test():
    load_dotenv()
    token = os.getenv("INDMONEY_TOKEN")
    url = "wss://ws-prices.indstocks.com/api/v1/ws/prices"
    
    print(f"Connecting to {url}...")
    try:
        # Try with additional_headers as documented in help
        async with websockets.connect(
            url, 
            additional_headers={"Authorization": token},
            ping_interval=30,
            ping_timeout=10
        ) as ws:
            print("Connected with additional_headers!")
            subscribe = {
                "action": "subscribe",
                "mode": "quote",
                "instruments": ["NIDX:40000001"]
            }
            await ws.send(json.dumps(subscribe))
            print("Subscription sent!")
            
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
                print(f"Received: {msg}")
            except asyncio.TimeoutError:
                print("Timeout waiting for message")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test())

import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

async def test_openai():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY is missing")
        return

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You are a trading assistant."},
            {"role": "user", "content": "Return 'OK' in JSON format."},
        ],
        "response_format": {"type": "json_object"}
    }
    
    print(f"Testing OpenAI with key: {api_key[:10]}...")
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            print(f"Status Code: {response.status_code}")
            print(f"Response: {response.text}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_openai())

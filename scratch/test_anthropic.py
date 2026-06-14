import asyncio
import os
import sys
from dotenv import load_dotenv

# Load env variables from the root folder .env
dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.env"))
load_dotenv(dotenv_path)

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.chartedge_core.ai_signal import AnthropicProvider

async def test():
    provider = AnthropicProvider({
        "model": "claude-sonnet-4-20250514",
        "temperature": 0.1,
        "max_tokens": 500
    })
    
    print(f"API key loaded: {os.getenv('ANTHROPIC_API_KEY')[:15]}...")
    try:
        response = await provider.complete("Test prompt: Say hello!", "Test system prompt")
        print("Success response:")
        print(response)
    except Exception as e:
        print(f"Failed with exception: {e}")

if __name__ == "__main__":
    asyncio.run(test())

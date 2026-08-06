import urllib.request
import json

TOKEN = "8740074056:AAHJ17rla5FAhS3TbsFbzSIlmFEaIQ4mKlc"
url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"

try:
    print(f"Fetching updates from: {url}")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        print(json.dumps(data, indent=2))
        
        results = data.get("result", [])
        if not results:
            print("\n❌ No updates found. Please search for your bot on Telegram and send it a /start message first, then run this script again!")
        else:
            print("\n✅ Found updates:")
            for r in results:
                msg = r.get("message") or r.get("channel_post")
                if msg:
                    chat = msg.get("chat", {})
                    chat_id = chat.get("id")
                    chat_title = chat.get("title") or chat.get("username") or chat.get("first_name")
                    print(f"   - Chat Name/Title: {chat_title} | Chat ID: {chat_id}")
except Exception as e:
    print(f"Error fetching updates: {e}")

import os
import json
import urllib.request
import urllib.parse
import asyncio
from typing import Optional

class TelegramNotifier:
    def __init__(self):
        # Read from environment
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "8740074056:AAFi3qKifRyMw9hsIwU1qC6Qqez2DqwA4Qo")
        self._chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        # Local cache file for resolved chat ID
        self.cache_file = ".telegram_chat_id"
        if not self._chat_id and os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    self._chat_id = f.read().strip()
                    print(f"DEBUG: Loaded cached Telegram Chat ID: {self._chat_id}")
            except Exception as e:
                print(f"DEBUG: Failed to read cached Chat ID: {e}")

    @property
    def chat_id(self) -> Optional[str]:
        return self._chat_id

    async def resolve_chat_id(self) -> Optional[str]:
        """Poll getUpdates to automatically find the first chat that has messaged the bot."""
        if self._chat_id:
            return self._chat_id
            
        print("DEBUG: Starting background loop to resolve Telegram Chat ID...")
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        
        while not self._chat_id:
            try:
                def fetch():
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return json.loads(response.read().decode())

                data = await asyncio.to_thread(fetch)
                results = data.get("result", [])
                if results:
                    # Find the latest message or group event
                    for r in reversed(results):
                        chat = None
                        
                        # 1. Try message or channel post
                        msg = r.get("message") or r.get("channel_post")
                        if msg:
                            chat = msg.get("chat", {})
                        
                        # 2. Try my_chat_member event (e.g., added to a group/channel)
                        mcm = r.get("my_chat_member")
                        if mcm and not chat:
                            chat = mcm.get("chat", {})
                            
                        if chat:
                            cid = str(chat.get("id"))
                            if cid:
                                self._chat_id = cid
                                # Cache it
                                try:
                                    with open(self.cache_file, "w") as f:
                                        f.write(cid)
                                except Exception as ce:
                                    print(f"DEBUG: Error writing chat ID cache: {ce}")
                                
                                # Send validation/greeting message
                                await self.send_message(
                                    "🤖 *ChartEdge AI Bot Initialized*\n\n"
                                    "✅ Connection successful! Real-time alerts for live trades will be posted in this group."
                                )
                                print(f"DEBUG: Successfully resolved Telegram Chat ID: {self._chat_id}")
                                return cid
            except Exception as e:
                print(f"Error resolving Telegram chat ID: {e}")
                
            # Poll every 3 seconds
            await asyncio.sleep(3)
            
        return self._chat_id

    async def send_message(self, text: str) -> bool:
        """Send a markdown-formatted message to the registered chat ID."""
        if not self.bot_token:
            return False
            
        # Dynamically resolve if not present
        if not self._chat_id:
            await self.resolve_chat_id()
            
        if not self._chat_id:
            print("⚠️ Telegram Chat ID not resolved yet. Send a message (e.g. /start) to the bot on Telegram first!")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        
        try:
            def post():
                data = urllib.parse.urlencode(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    return response.read()

            await asyncio.to_thread(post)
            return True
        except Exception as e:
            print(f"Error sending message to Telegram: {e}")
            return False

# Global notifier instance
notifier = TelegramNotifier()

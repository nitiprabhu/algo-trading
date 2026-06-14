import asyncio
from services.chartedge_core.telegram import notifier

async def main():
    print("📢 Testing ChartEdge Telegram Notifications...")
    print("--------------------------------------------------")
    print(f"Token: {notifier.bot_token[:10]}...{notifier.bot_token[-10:]}")
    print(f"Initial Chat ID: {notifier.chat_id}")
    
    if not notifier.chat_id:
        print("\n🔍 Polling /getUpdates to auto-discover Chat ID...")
        print("💡 STEP: Please open Telegram, search for your bot, and send a message (like /start) NOW!")
        print("⌛ Waiting for 15 seconds...")
        for i in range(15):
            print(f"\rRemaining: {15-i}s...", end="", flush=True)
            cid = await notifier.resolve_chat_id()
            if cid:
                print(f"\n\n🎉 SUCCESS! Resolved Chat ID: {cid}")
                break
            await asyncio.sleep(1)
        else:
            print("\n\n❌ Chat ID not resolved. Please ensure you sent a message to the bot and try again.")
            return
    else:
        print("\n✅ Sending test trade message to Telegram...")
        success = await notifier.send_message(
            "🔔 *TEST TRADE NOTIFICATION*\n\n"
            "🌐 *Instrument:* `NIFTY-May2026-23650-CE`\n"
            "📈 *Direction:* `BUY`\n"
            "💰 *Entry Price:* `₹285.50`\n"
            "📦 *Quantity:* `150`\n"
            "🛡️ *Stop Loss:* `₹245.00`\n"
            "🎯 *Target:* `₹340.00`\n\n"
            "🧠 *Reason:* Confluence threshold cross-over to bullish territory + EMA cross-up."
        )
        if success:
            print("🚀 Test message sent successfully!")
        else:
            print("❌ Failed to send test message.")

if __name__ == "__main__":
    asyncio.run(main())

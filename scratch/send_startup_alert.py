import asyncio
from services.chartedge_core.telegram import notifier

async def main():
    msg = (
        "🤖 *ChartEdge Command Listener Active!*\n\n"
        "You can now send the following commands to check status:\n"
        "📊 `/positions` - View active trades & legs\n"
        "💰 `/pnl` - View realized & unrealized PnL\n"
        "🏥 `/status` - Check server health"
    )
    success = await notifier.send_message(msg)
    if success:
        print("Telegram notification sent successfully!")
    else:
        print("Failed to send Telegram notification.")

if __name__ == "__main__":
    asyncio.run(main())

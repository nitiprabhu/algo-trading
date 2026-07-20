import sys
import os
import asyncio

# Load path
sys.path.insert(0, '/Users/nithish-prabhu/Downloads/intra-day')

from services.chartedge_core.telegram import TelegramNotifier

def escape_markdown(text: str) -> str:
    # Escape characters that are special in Telegram markdown: _, *, [, ], `
    for char in ['_', '*', '[', ']', '`']:
        text = text.replace(char, f'\\{char}')
    return text

async def main():
    print("Sending Telegram notifications for the manual execution with escaped markdown...")
    notifier = TelegramNotifier()
    
    # 1. LAURUSLABS
    msg_laurus = (
        "[LIVE ORDER] BUY LAURUSLABS x6 tag=POS_MIDCAP | "
        "entry placed + GTT GTT-C26200700212467"
    )
    escaped_laurus = escape_markdown(msg_laurus)
    print(f"Sending: {escaped_laurus}")
    res_laurus = await notifier.send_message(escaped_laurus)
    print(f"  Result: {res_laurus}")

    # 2. NYKAA
    msg_nykaa = (
        "[LIVE ORDER] BUY NYKAA x29 tag=POS_MIDCAP | "
        "entry placed + GTT GTT-C26200700214590"
    )
    escaped_nykaa = escape_markdown(msg_nykaa)
    print(f"Sending: {escaped_nykaa}")
    res_nykaa = await notifier.send_message(escaped_nykaa)
    print(f"  Result: {res_nykaa}")

if __name__ == "__main__":
    asyncio.run(main())

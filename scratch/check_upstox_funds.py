import sys
import os

# Load path
sys.path.insert(0, '/Users/nithish-prabhu/Downloads/intra-day')

from services.chartedge_core.upstox_broker import live_broker

broker = live_broker()
token = broker.get_valid_token()

if not token:
    print("Error: No valid Upstox token found for today!")
    sys.exit(1)

print(f"Token found: {token[:20]}...")
funds = broker.get_available_funds(token)
if funds is not None:
    print(f"Available Margin/Funds: ₹{funds:,.2f}")
else:
    print("Error: Failed to fetch available funds!")

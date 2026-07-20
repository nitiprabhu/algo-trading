import sys
import os
import socket
import urllib3.util.connection as urllib3_cn

# Force urllib3 / requests to resolve over IPv4 only
# This avoids Upstox seeing our IPv6 address and triggering a whitelist mismatch.
def allowed_gai_family():
    return socket.AF_INET

urllib3_cn.allowed_gai_family = allowed_gai_family

# Load path
sys.path.insert(0, '/Users/nithish-prabhu/Downloads/intra-day')

from services.chartedge_core.config import load_config
from services.chartedge_core.upstox_broker import live_broker

def main():
    if len(sys.argv) < 3:
        print("Usage: python execute_trades.py <SYMBOL> <QUANTITY> <REF_PRICE> <POOL>")
        print("Example: python execute_trades.py LAURUSLABS 16 1561.20 midcap")
        sys.exit(1)

    symbol = sys.argv[1]
    quantity = int(sys.argv[2])
    ref_price = float(sys.argv[3])
    pool = sys.argv[4].lower()

    # Load configuration
    config = load_config()

    # Initialize live broker with live_trading configuration
    broker = live_broker(config.live_trading)
    token = broker.get_valid_token()

    if not token:
        print("Error: No valid Upstox token found for today!")
        sys.exit(1)

    tag = f"POS_{pool.upper()}"
    print(f"Initializing trade execution (Forced IPv4):")
    print(f"  Symbol:    {symbol}")
    print(f"  Quantity:  {quantity} (Note: Will auto-size down if funds are insufficient)")
    print(f"  Ref Price: {ref_price}")
    print(f"  Tag:       {tag}")
    print(f"  Armed:     {broker.is_armed()}")

    # Call place_entry
    result = broker.place_entry(
        symbol=symbol,
        quantity=quantity,
        ref_price=ref_price,
        tag=tag
    )

    print("\nExecution Result:")
    print(f"  Success:   {result.ok}")
    print(f"  Simulated: {result.simulated}")
    print(f"  Order ID:  {result.order_id}")
    print(f"  GTT ID:    {result.gtt_id}")
    print(f"  Avg Price: {result.avg_price}")
    print(f"  Reason:    {result.reason}")

if __name__ == "__main__":
    main()

import sys
import os
import socket
import urllib3.util.connection as urllib3_cn

# Force IPv4
def allowed_gai_family():
    return socket.AF_INET
urllib3_cn.allowed_gai_family = allowed_gai_family

# Load path
sys.path.insert(0, '/Users/nithish-prabhu/Downloads/intra-day')

from services.chartedge_core.config import load_config
from services.chartedge_core.upstox_broker import live_broker

def main():
    config = load_config()
    broker = live_broker(config.live_trading)
    token = broker.get_valid_token()
    
    if not token:
        print("Error: No valid token found!")
        sys.exit(1)
        
    symbol = "NYKAA"
    quantity = 29
    ref_price = 324.50
    instrument = broker.instrument_keys.get(symbol)
    
    print(f"Placing GTT stop-loss for {symbol}:")
    print(f"  Instrument: {instrument}")
    print(f"  Quantity:   {quantity}")
    print(f"  Ref Price:  {ref_price}")
    
    gtt_id = broker._place_gtt_stop(symbol, instrument, quantity, ref_price, token)
    print(f"GTT stop-loss placed successfully: {gtt_id}")

if __name__ == "__main__":
    main()

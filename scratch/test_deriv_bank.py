
import os
import sys
import pandas as pd
from datetime import datetime

# Add current directory to path
sys.path.append(os.getcwd())

from services.chartedge_core.derivative_manager import DerivativeManager

def test_resolution():
    token = os.getenv("INDMONEY_TOKEN") or "dummy"
    dm = DerivativeManager(token)
    
    symbol = "BANKNIFTY"
    spot = 48230.0
    
    print(f"\n--- Testing {symbol} with range_strikes=10 ---")
    chain = dm.get_option_chain(spot, symbol, range_strikes=10)
    print(f"Spot: {spot}, Chain length: {len(chain)}")
    if chain:
        for row in chain:
            print(row)
    else:
        print("No chain resolved!")

if __name__ == "__main__":
    test_resolution()

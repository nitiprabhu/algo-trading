
import os
import sys
import pandas as pd
from datetime import datetime

print("Script started...")

# Add current directory to path
sys.path.append(os.getcwd())

try:
    from services.chartedge_core.derivative_manager import DerivativeManager
    print("Import successful")
except Exception as e:
    print(f"Import failed: {e}")
    sys.exit(1)

def test_resolution():
    token = os.getenv("INDMONEY_TOKEN") or "dummy"
    print(f"Token length: {len(token)}")
    
    dm = DerivativeManager(token)
    print("DerivativeManager initialized")
    
    # Manually trigger master fetch if not loaded
    if dm._fno_df is None:
        print("Fetching master...")
        dm._fetch_fno_master()
    
    if dm._fno_df is not None:
        print(f"Master loaded. Shape: {dm._fno_df.shape}")
        # Check columns
        print(f"Columns: {dm._fno_df.columns.tolist()}")
    else:
        print("Master FAILED to load!")
        return

    symbols = ["NIFTY", "BANKNIFTY"]
    spots = {"NIFTY": 22450.0, "BANKNIFTY": 48230.0}
    
    for symbol in symbols:
        print(f"\n--- Testing {symbol} ---")
        spot = spots[symbol]
        try:
            chain = dm.get_option_chain(spot, symbol)
            print(f"Spot: {spot}, Chain length: {len(chain)}")
            if chain:
                for row in chain[:2]: # Just first 2
                    print(row)
            else:
                print("No chain resolved!")
        except Exception as e:
            print(f"Error in get_option_chain for {symbol}: {e}")

if __name__ == "__main__":
    test_resolution()

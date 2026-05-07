
import os
import pandas as pd
from services.chartedge_core.derivative_manager import DerivativeManager
from dotenv import load_dotenv

load_dotenv()

dm = DerivativeManager("DUMMY")
# Assuming the cache exists from previous runs
try:
    # Let's try to mock a spot price
    spot = 22850
    opts = dm.get_atm_options(spot, "NIFTY")
    print("NIFTY ATM Options:")
    print(opts)
    
    spot_bank = 48600
    opts_bank = dm.get_atm_options(spot_bank, "BANKNIFTY")
    print("\nBANKNIFTY ATM Options:")
    print(opts_bank)
except Exception as e:
    print(f"Error: {e}")

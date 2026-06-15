import os
from datetime import datetime
from services.chartedge_core.config import load_config
from services.chartedge_core.derivative_manager import DerivativeManager

config = load_config()
dm = DerivativeManager(config)

spot = 23960.0
print(f"Spot price: {spot}")

# Interval is 50 for NIFTY
# ATM is 23950 or 24000? 
# 23960 % 50 = 10, remainder is < 25, so ATM is 23950.

# Let's call get_atm_options directly for various strike offsets
# strike_offset = -1:
# ce_strike = atm_strike - (-1 * 50) = atm_strike + 50 = 24000
# pe_strike = atm_strike + (-1 * 50) = atm_strike - 50 = 23900

# Let's print ATM options at various offsets
for offset in [0, -1, -3]:
    opts = dm.get_atm_options(spot, "NIFTY", current_dt=datetime(2026, 6, 15, 12, 0, 0), strike_offset=offset)
    print(f"\nOffset {offset}:")
    if 'CE' in opts:
        print(f"  CE Leg: {opts['CE']['symbol']} | Strike: {opts['CE']['strike']}")
    if 'PE' in opts:
        print(f"  PE Leg: {opts['PE']['symbol']} | Strike: {opts['PE']['strike']}")

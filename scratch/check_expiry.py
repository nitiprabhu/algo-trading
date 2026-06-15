import os
from services.chartedge_core.config import load_config
from services.chartedge_core.derivative_manager import DerivativeManager

config = load_config()
dm = DerivativeManager(config)
suffix = dm.get_futures_expiry_suffix("NIFTY")
print(f"NIFTY suffix: {suffix}")
suffix_bank = dm.get_futures_expiry_suffix("BANKNIFTY")
print(f"BANKNIFTY suffix: {suffix_bank}")

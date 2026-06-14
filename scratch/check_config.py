import os
from services.chartedge_core.config import load_config
config = load_config(os.path.abspath("shared/config.yaml"))
print(f"max_loss = {config.risk.get('options_max_loss_pct')}")

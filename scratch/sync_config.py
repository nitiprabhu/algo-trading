import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.chartedge_core.config import load_config, sync_config_to_db

if __name__ == "__main__":
    config = load_config()
    sync_config_to_db(config)
    print("✅ Config synced to DB.")

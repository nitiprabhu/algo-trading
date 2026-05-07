import json
from pathlib import Path
import os

def cleanup_logs(log_file="logs/training_data.jsonl"):
    root = Path("/Users/nithish-prabhu/Downloads/intra-day")
    input_path = root / log_file
    temp_path = root / f"{log_file}.tmp"
    
    if not input_path.exists():
        print("Log file not found.")
        return

    bad_count = 0
    with open(input_path, "r") as f_in, open(temp_path, "w") as f_out:
        for line in f_in:
            data = json.loads(line)
            # Filter criteria: P&L > 100k or P&L % > 100%
            pnl = data.get("pnl", 0)
            pnl_pct = data.get("pnl_pct", 0)
            
            if abs(pnl) > 100000 or abs(pnl_pct) > 100:
                bad_count += 1
                continue
            
            f_out.write(line)
            
    os.replace(temp_path, input_path)
    print(f"✅ Cleaned logs. Removed {bad_count} corrupted entries.")

if __name__ == "__main__":
    cleanup_logs()

import json
import csv
from pathlib import Path

def prepare_ml_dataset(log_file="logs/training_data.jsonl", output_file="logs/ml_dataset.csv"):
    root = Path(__file__).resolve().parents[1]
    input_path = root / log_file
    output_path = root / output_file
    
    if not input_path.exists():
        print(f"Error: {log_file} not found.")
        return

    dataset = []
    
    # We need to pair ENTRY and EXIT events by trade_id
    entries = {}
    
    with open(input_path, "r") as f:
        for line in f:
            data = json.loads(line)
            trade_id = data.get("trade_id")
            
            if data["event"] == "ENTRY":
                entries[trade_id] = data
            elif data["event"] == "EXIT" and trade_id in entries:
                entry = entries[trade_id]
                
                # Flatten the feature set
                features = entry.get("features", {})
                indicators = features.get("indicators", {})
                market_ctx = features.get("market_context", {}) or {}
                options_data = features.get("options_data", {}) or {}
                
                row = {
                    "trade_id": trade_id,
                    "symbol": entry["symbol"],
                    "direction": entry["direction"],
                    "entry_price": entry["entry_price"],
                    "confluence": features.get("confluence", 0),
                    "confidence": features.get("confidence", 0),
                    
                    # Market Context
                    "vix": market_ctx.get("india_vix", 0),
                    "basis": market_ctx.get("basis", 0),
                    "reliance_trend": market_ctx.get("reliance_trend", "NEUTRAL"),
                    "hdfc_trend": market_ctx.get("hdfc_bank_trend", "NEUTRAL"),
                    
                    # Options
                    "pcr": options_data.get("pcr", 0),
                    "oi_change": options_data.get("oi_change_pct", 0),
                    
                    # Core Indicators (Values)
                    "rsi": indicators.get("rsi", {}).get("value", 50),
                    "macd_hist": indicators.get("macd", {}).get("value", {}).get("hist", 0),
                    "atr": indicators.get("atr", {}).get("value", 0),
                    
                    # Outcome (Labels)
                    "exit_reason": data["exit_reason"],
                    "pnl": data["pnl"],
                    "pnl_pct": data["pnl_pct"],
                    "duration": data["duration_seconds"],
                    "is_win": 1 if data["pnl"] > 0 else 0
                }
                dataset.append(row)

    if not dataset:
        print("No paired trades found to export.")
        return

    # Write to CSV
    keys = dataset[0].keys()
    with open(output_path, "w", newline="") as f:
        dict_writer = csv.DictWriter(f, fieldnames=keys)
        dict_writer.writeheader()
        dict_writer.writerows(dataset)
        
    print(f"✅ Success! ML dataset created at {output_file}")
    print(f"📊 Total samples: {len(dataset)}")

if __name__ == "__main__":
    prepare_ml_dataset()

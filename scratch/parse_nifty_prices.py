import json

count = 0
with open("nohup.out", "r") as f:
    for line in f:
        if "Raw message received:" in line and "40000001" in line:
            try:
                # Extract json string
                start_idx = line.find('received: "') + 11
                end_idx = line.rfind('"')
                raw_str = line[start_idx:end_idx]
                raw_str = raw_str.replace('\\"', '"')
                payload = json.loads(raw_str)
                print("NIFTY tick:", payload)
                count += 1
                if count >= 5:
                    break
            except Exception as e:
                # Try simple regex or manual substring if json load fails
                print("Line failed:", line[:150], "Error:", e)

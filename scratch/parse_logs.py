import json

with open("nohup.out", "r") as f:
    for line in f:
        if "Raw message received:" in line:
            # Extract json string
            try:
                start_idx = line.find('received: "') + 11
                end_idx = line.rfind('"')
                raw_str = line[start_idx:end_idx]
                # Replace escaped double quotes
                raw_str = raw_str.replace('\\"', '"')
                payload = json.loads(raw_str)
                print("RAW PAYLOAD:", payload)
                break
            except Exception as e:
                print("Error:", e)
                print("Line was:", line)

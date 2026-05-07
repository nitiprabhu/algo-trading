import json

nifty_ticks = []
banknifty_ticks = []
other_ticks = 0

with open("nohup.out", "r") as f:
    for line in f:
        if "Raw message received:" in line:
            try:
                # Find the instrument
                start_idx = line.find('instrument\\":\\"')
                if start_idx != -1:
                    start_idx += 15
                    end_idx = line.find('\\"', start_idx)
                    inst = line[start_idx:end_idx]
                else:
                    # try without escaped double quote
                    start_idx = line.find('instrument":"')
                    if start_idx != -1:
                        start_idx += 13
                        end_idx = line.find('"', start_idx)
                        inst = line[start_idx:end_idx]
                    else:
                        continue
                
                # Get timestamp
                ts_idx = line.find('timestamp\\":')
                if ts_idx != -1:
                    ts_idx += 12
                    ts_end = line.find(',', ts_idx)
                    ts = int(line[ts_idx:ts_end])
                else:
                    ts_idx = line.find('timestamp":')
                    if ts_idx != -1:
                        ts_idx += 11
                        ts_end = line.find(',', ts_idx)
                        ts = int(line[ts_idx:ts_end])
                    else:
                        ts = 0

                if inst == "40000001":
                    nifty_ticks.append(ts)
                elif inst == "40000003":
                    banknifty_ticks.append(ts)
                else:
                    other_ticks += 1
            except Exception as e:
                pass

print(f"NIFTY Ticks Count: {len(nifty_ticks)}")
print(f"BANKNIFTY Ticks Count: {len(banknifty_ticks)}")
print(f"Other Ticks Count: {other_ticks}")

if nifty_ticks:
    from datetime import datetime
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
    print("First NIFTY tick:", datetime.fromtimestamp(nifty_ticks[0]/1000, IST))
    print("Last NIFTY tick:", datetime.fromtimestamp(nifty_ticks[-1]/1000, IST))
if banknifty_ticks:
    print("First BANKNIFTY tick:", datetime.fromtimestamp(banknifty_ticks[0]/1000, IST))
    print("Last BANKNIFTY tick:", datetime.fromtimestamp(banknifty_ticks[-1]/1000, IST))

import json

with open("nohup.out", "r") as f:
    for line in f:
        if "Raw message received:" in line and "40000001" in line:
            print("Found line containing 40000001:")
            print(line[:250])
            break

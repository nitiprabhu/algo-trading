import re

with open("services/chartedge_core/paper_trading.py", "r") as f:
    content = f.read()

# Replace startswith NAKED_BUY with generic colon check
content = content.replace('signal.instrument.startswith("NAKED_BUY:")', '":" in signal.instrument')
content = content.replace('trade.instrument.startswith("NAKED_BUY:")', '":" in trade.instrument')
content = content.replace('is_naked_buy', 'is_multi_leg')
content = content.replace('is_naked', 'is_multi_leg')

with open("services/chartedge_core/paper_trading.py", "w") as f:
    f.write(content)

print("Patched!")

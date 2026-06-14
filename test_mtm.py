import re
with open("services/chartedge_core/paper_trading.py", "r") as f:
    content = f.read()
if "BS computed premium" in content:
    content = content.replace('# print(f"🔄 [MTM] {trade.instrument}: BS computed premium={current_price:.2f} (entry={trade.entry_price:.2f})")',
                              'print(f"🔄 [MTM] {trade.instrument}: BS computed premium={current_price:.2f} (entry={trade.entry_price:.2f})")')
with open("services/chartedge_core/paper_trading.py", "w") as f:
    f.write(content)

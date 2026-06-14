import re
with open("services/chartedge_core/paper_trading.py", "r") as f:
    content = f.read()

debug_print = """                            if is_multi_leg:
                                print(f"DEBUG MTM: instrument={trade.instrument} is_multi_leg={is_multi_leg} legs={len(trade.legs) if hasattr(trade, 'legs') else 'NO_ATTR'}")
                            if is_multi_leg and trade.legs:"""

content = content.replace("                            if is_multi_leg and trade.legs:", debug_print)

with open("services/chartedge_core/paper_trading.py", "w") as f:
    f.write(content)

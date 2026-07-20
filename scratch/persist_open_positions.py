import sys
import os

# Load path
sys.path.insert(0, '/Users/nithish-prabhu/Downloads/intra-day')

from services.chartedge_core.database import persist_stock_entry

print("Recording positions in database:")

# 1. LAURUSLABS
rec_laurus = persist_stock_entry(
    symbol="LAURUSLABS",
    entry_date="2026-07-20",
    entry_price=1561.20,
    quantity=6,
    pool="midcap"
)
if rec_laurus:
    print(f"  Successfully recorded LAURUSLABS: ID={rec_laurus.position_id}")
else:
    print("  Failed to record LAURUSLABS")

# 2. NYKAA
rec_nykaa = persist_stock_entry(
    symbol="NYKAA",
    entry_date="2026-07-20",
    entry_price=324.50,
    quantity=29,
    pool="midcap"
)
if rec_nykaa:
    print(f"  Successfully recorded NYKAA: ID={rec_nykaa.position_id}")
else:
    print("  Failed to record NYKAA")

import os
import pandas as pd
from dotenv import load_dotenv
from services.chartedge_core.derivative_manager import DerivativeManager

load_dotenv()
token = os.getenv("INDMONEY_TOKEN")
dm = DerivativeManager(token)

dm.get_option_chain(24000, "NIFTY")

df = dm._fno_df
print(f"Columns: {df.columns.tolist()}")

# Look for NIFTY in all columns
for col in ['TRADING_SYMBOL', 'CUSTOM_SYMBOL', 'SYMBOL_NAME']:
    if col in df.columns:
        matches = df[df[col].str.contains('NIFTY', case=False, na=False)]
        print(f"Matches in {col}: {len(matches)}")
        if len(matches) > 0:
            print(f"Sample {col}: {matches[col].unique()[:5]}")

# Check NIFTY 50 specific name
nifty_options = df[(df['INSTRUMENT_NAME'] == 'OPTIDX') & (df['TRADING_SYMBOL'].str.contains('NIFTY', case=False, na=False))]
print(f"\nNIFTY Index Options (by TRADING_SYMBOL): {len(nifty_options)}")
if len(nifty_options) > 0:
    print(nifty_options[['TRADING_SYMBOL', 'EXPIRY_DATE', 'STRIKE_PRICE', 'OPTION_TYPE']].head(5))

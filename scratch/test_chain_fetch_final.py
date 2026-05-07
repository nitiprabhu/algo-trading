import os
from dotenv import load_dotenv
from services.chartedge_core.derivative_manager import DerivativeManager

load_dotenv()
token = os.getenv("INDMONEY_TOKEN")
dm = DerivativeManager(token)

print("Fetching Nifty Option Chain for spot 24000...")
chain = dm.get_option_chain(24000, "NIFTY")

print(f"Chain length: {len(chain)}")
if len(chain) > 0:
    print("\nFirst 3 rows of chain:")
    for row in chain[:3]:
        print(row)
else:
    print("STILL EMPTY. Checking why...")
    df = dm._fno_df
    pattern = "^NIFTY-"
    mask = (df['INSTRUMENT_NAME'] == 'OPTIDX') & (df['TRADING_SYMBOL'].str.match(pattern, case=False, na=False))
    opts = df[mask].copy()
    print(f"Masked options count: {len(opts)}")
    if len(opts) > 0:
        opts['dt'] = pd.to_datetime(opts['EXPIRY_DATE'], errors='coerce')
        min_date = opts['dt'].min()
        print(f"Min expiry date: {min_date}")
        near_opts = opts[opts['dt'] == min_date].copy()
        print(f"Near expiry options count: {len(near_opts)}")
        print("Near expiry strikes sample:", near_opts['STRIKE_PRICE'].unique()[:10])

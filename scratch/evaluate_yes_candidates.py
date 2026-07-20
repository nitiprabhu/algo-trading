import sys
import os
import pandas as pd
import yfinance as yf

# Load path
sys.path.insert(0, '/Users/nithish-prabhu/Downloads/intra-day')

from services.chartedge_core.config import load_config
config = load_config()

from services.chartedge_core.positional_stocks_runtime import fetch_daily_candles
from services.chartedge_core.positional_stocks import compute_stock_signal

symbols = ["LAURUSLABS", "NYKAA", "INDIGRID", "HFCL"]

print(f"{'Symbol':<15} | {'Confluence Score':<18} | {'ADX':<8} | {'EMA Ribbon':<12} | {'Supertrend':<12}")
print("-" * 75)

for symbol in symbols:
    try:
        daily = fetch_daily_candles(symbol, period="2y")
        if len(daily) < 20:
            print(f"{symbol:<15} | {'Too little data':<18}")
            continue
        
        # Pass empty dict for weights so it uses defaults
        score, indicators = compute_stock_signal(daily, {})
        adx_value = indicators["adx"].value
        ema_vote = indicators["ema_ribbon"].vote
        st_vote = indicators["supertrend"].vote
        
        print(f"{symbol:<15} | {score:<18.3f} | {adx_value:<8.1f} | {ema_vote:<12} | {st_vote:<12}")
    except Exception as e:
        print(f"{symbol:<15} | Error: {str(e)[:40]}")

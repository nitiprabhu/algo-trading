import yfinance as yf

symbols = {
    "LAURUSLABS": 1560.7,
    "ADANIENSOL": 1690.3,
    "FEDERALBNK": 327.5,
    "NYKAA": 324.45,
    "DIACABS": 224.97,
    "INDIGRID": 179.52,
    "APCOTEXIND": 527.9,
    "HFCL": 213.17
}

print(f"{'Symbol':<15} | {'Entry Price':<12} | {'Current Price':<13} | {'Diff (%)':<10}")
print("-" * 60)

for sym, entry in symbols.items():
    ticker = f"{sym}.NS"
    try:
        data = yf.Ticker(ticker)
        # Fetch fast info or regular history for current price
        # Using history to get the last price reliably
        hist = data.history(period="1d")
        if not hist.empty:
            curr = hist['Close'].iloc[-1]
            diff_pct = ((curr - entry) / entry) * 100
            print(f"{sym:<15} | {entry:<12.2f} | {curr:<13.2f} | {diff_pct:<+10.2f}%")
        else:
            print(f"{sym:<15} | {entry:<12.2f} | {'No Data':<13} | N/A")
    except Exception as e:
        print(f"{sym:<15} | {entry:<12.2f} | Error: {str(e)[:15]}...")

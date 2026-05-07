from services.chartedge_core.api import runtime

print("NIFTY latest candle:")
if "NIFTY" in runtime.candles and runtime.candles["NIFTY"]:
    print(runtime.candles["NIFTY"][-1])
else:
    print("No NIFTY candles")

print("INDIAVIX latest candle:")
if "INDIAVIX" in runtime.candles and runtime.candles["INDIAVIX"]:
    print(runtime.candles["INDIAVIX"][-1])
else:
    print("No INDIAVIX candles")

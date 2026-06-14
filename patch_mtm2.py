import re

with open("services/chartedge_core/paper_trading.py", "r") as f:
    content = f.read()

old_block = """                        # BS estimate
                        underlying_sym = "BANKNIFTY" if "BANKNIFTY" in trade.instrument.upper() else "NIFTY"
                        if candle.instrument == underlying_sym:
                            # We don't have bs_delta here, so use proxy 0.5
                            delta = 0.5
                            dir_mult = -1 if leg.option_type == "PE" else 1
                            underlying_move = candle.close - trade.underlying_entry_price
                            leg_price = max(0.01, round(leg.entry_price + delta * dir_mult * underlying_move, 2))"""

new_block = """                        # BS estimate
                        underlying_sym = "BANKNIFTY" if "BANKNIFTY" in trade.instrument.upper() else "NIFTY"
                        if candle.instrument == underlying_sym:
                            vix = 14.0 # default fallback
                            if hasattr(self, "candles") and "INDIAVIX" in self.candles and self.candles["INDIAVIX"]:
                                vix = self.candles["INDIAVIX"][-1].close
                            dte = self._dte_to_expiry(underlying_sym, candle.time)
                            iv = iv_from_vix(vix, dte)
                            leg_price = bs_price(candle.close, leg.strike, dte, iv, leg.option_type)
                            leg_price = max(0.01, round(leg_price, 2))"""

if old_block in content:
    content = content.replace(old_block, new_block)
    with open("services/chartedge_core/paper_trading.py", "w") as f:
        f.write(content)
    print("Patch applied successfully.")
else:
    print("Warning: old_block not found.")

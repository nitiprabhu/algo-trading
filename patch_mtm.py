import re

with open("services/chartedge_core/paper_trading.py", "r") as f:
    content = f.read()

# 1. Add imports at top
import_str = "from services.chartedge_core.option_data import bs_price, iv_from_vix\nfrom datetime import timedelta\n"
if "bs_price" not in content:
    content = content.replace("from services.chartedge_core.costs import", import_str + "from services.chartedge_core.costs import")

# 2. Add _dte_to_expiry helper to PaperTradingEngine if not exists
dte_helper = """
    def _dte_to_expiry(self, symbol: str, now: datetime) -> float:
        underlying = ("BANKNIFTY" if "BANKNIFTY" in symbol.upper() else "NIFTY" if "NIFTY" in symbol.upper() else symbol.upper())
        cfg = self.expiry_map.get(underlying, self.expiry_map.get("DEFAULT", {"weekly_weekday": 1, "monthly_weekday": 1}))
        d = now.date()
        weekly_wd = cfg.get("weekly_weekday")
        if weekly_wd is not None:
            expiry = d + timedelta(days=(weekly_wd - d.weekday()) % 7)
        else:
            monthly_wd = cfg.get("monthly_weekday", 1)
            import calendar as _cal
            last = _cal.monthrange(d.year, d.month)[1]
            expiry = d
            for day in range(last, last - 7, -1):
                if datetime(d.year, d.month, day).weekday() == monthly_wd:
                    expiry = datetime(d.year, d.month, day).date()
                    break
            if expiry < d:
                ny, nm = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
                last = _cal.monthrange(ny, nm)[1]
                expiry = d
                for day in range(last, last - 7, -1):
                    if datetime(ny, nm, day).weekday() == monthly_wd:
                        expiry = datetime(ny, nm, day).date()
                        break
        # Calculate strict DTE including time fraction
        expiry_dt = datetime.combine(expiry, time(15, 30))
        if hasattr(now, "tzinfo") and now.tzinfo:
            expiry_dt = expiry_dt.replace(tzinfo=now.tzinfo)
        diff = (expiry_dt - now).total_seconds() / 86400.0
        return max(0.001, diff)

"""
if "def _dte_to_expiry" not in content:
    content = content.replace("    def load_active_trades", dte_helper + "    def load_active_trades")


# 3. Replace MTM calculation
old_mtm = """                            underlying_move = candle.close - trade.underlying_entry_price
                            current_price = max(0.01, round(trade.entry_price + delta * direction_mult * underlying_move, 2))
                            if is_multi_leg:
                                print(f"🔄 [MTM] {trade.instrument}: underlying moved {underlying_move:+.2f} → premium now {current_price:.2f} (entry={trade.entry_price:.2f})")"""

new_mtm = """                            underlying_move = candle.close - trade.underlying_entry_price
                            vix = 14.0 # default fallback
                            if "INDIAVIX" in self.candles and self.candles["INDIAVIX"]:
                                vix = self.candles["INDIAVIX"][-1].close
                            dte = self._dte_to_expiry(underlying_sym, candle.time)
                            iv = iv_from_vix(vix, dte)
                            
                            if is_multi_leg and trade.legs:
                                net_premium = 0.0
                                for leg in trade.legs:
                                    leg_price = bs_price(candle.close, leg.strike, dte, iv, leg.option_type)
                                    multiplier = 1 if leg.action == Direction.BUY else -1
                                    net_premium += (leg_price * leg.ratio * multiplier)
                                current_price = max(0.01, round(net_premium, 2))
                                # print(f"🔄 [MTM] {trade.instrument}: BS computed premium={current_price:.2f} (entry={trade.entry_price:.2f})")
                            else:
                                current_price = max(0.01, round(trade.entry_price + delta * direction_mult * underlying_move, 2))"""

if old_mtm in content:
    content = content.replace(old_mtm, new_mtm)
else:
    print("WARNING: Old MTM block not found! Did not replace.")

with open("services/chartedge_core/paper_trading.py", "w") as f:
    f.write(content)

print("Patch script complete.")

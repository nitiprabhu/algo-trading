import os
import httpx
import pandas as pd
import io
from typing import Dict, Optional, List
from datetime import datetime

class DerivativeManager:
    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://api.indstocks.com"
        self._fno_df: Optional[pd.DataFrame] = None
        self._cache_file = "/tmp/chartedge_fno_master.csv"
        self._last_fail_ts: float = 0.0  # epoch seconds of last API failure
        self._FAIL_COOLDOWN = 300  # don't retry for 5 minutes after a 4xx

    def _fetch_fno_master(self):
        # Don't hammer API after a recent failure — return immediately
        if self._last_fail_ts and (datetime.now().timestamp() - self._last_fail_ts) < self._FAIL_COOLDOWN:
            raise Exception(f"Failed to fetch F&O master: cooldown active (last fail {int(datetime.now().timestamp() - self._last_fail_ts)}s ago)")

        # Use cache if it exists and is fresh (less than 24 hours old)
        if os.path.exists(self._cache_file):
            mtime = os.path.getmtime(self._cache_file)
            if datetime.now().timestamp() - mtime < 86400:
                print("DEBUG: Loading F&O master from cache")
                self._fno_df = pd.read_csv(self._cache_file)
                print(f"DEBUG: F&O master loaded. Shape: {self._fno_df.shape}")
                return

        print("DEBUG: Fetching fresh F&O master from API")
        token = self.token
        if token.startswith("Bearer "):
            token = token[7:]
        headers = {"Authorization": token}
        r = httpx.get(f"{self.base_url}/market/instruments", params={"source": "fno"}, headers=headers, timeout=10)
        if r.status_code == 200:
            self._fno_df = pd.read_csv(io.StringIO(r.text))
            self._fno_df.to_csv(self._cache_file, index=False)
            self._last_fail_ts = 0.0
        else:
            self._last_fail_ts = datetime.now().timestamp()
            raise Exception(f"Failed to fetch F&O master: {r.status_code}")

    def get_current_future(self, underlying: str = "NIFTY") -> Optional[str]:
        if self._fno_df is None: self._fetch_fno_master()
        
        # Filter for index futures of the underlying
        pattern = f"^{underlying}-"
        mask = (self._fno_df['INSTRUMENT_NAME'] == 'FUTIDX') & (self._fno_df['TRADING_SYMBOL'].str.match(pattern, case=False, na=False))
        futs = self._fno_df[mask].copy()
        
        if futs.empty: return None
        
        # Convert EXPIRY_DATE to datetime and sort
        # Format in csv seems to be 'DD MMM YYYY' or 'MM/DD/YYYY HH:MM'
        futs['dt'] = pd.to_datetime(futs['EXPIRY_DATE'], errors='coerce')
        current_fut = futs.sort_values('dt').iloc[0]
        
        return f"NFO:{int(current_fut['SECURITY_ID'])}"

    def get_option_chain(self, spot_price: float, underlying: str = "NIFTY", range_strikes: int = 5, current_dt: Optional[datetime] = None, expiry_buffer_days: int = 1) -> List[Dict]:
        if self._fno_df is None: self._fetch_fno_master()
        
        # Symbol-aware strike interval
        interval = 100 if "BANK" in underlying else (25 if "MID" in underlying else 50)
        
        # Determine ATM strike
        remainder = spot_price % interval
        atm_strike = (spot_price - remainder + interval) if remainder >= (interval/2) else (spot_price - remainder)
        
        # Get strikes based on interval
        strikes = [atm_strike + (i * interval) for i in range(-range_strikes, range_strikes + 1)]
        # Filter for index options of the underlying
        pattern = f"^{underlying}-"
        mask = (self._fno_df['INSTRUMENT_NAME'] == 'OPTIDX') & (self._fno_df['TRADING_SYMBOL'].str.match(pattern, case=False, na=False))
        opts = self._fno_df[mask].copy()
        if opts.empty: return []
        
        opts['dt'] = pd.to_datetime(opts['EXPIRY_DATE'], errors='coerce')
        unique_expiries = sorted(opts['dt'].dropna().unique())
        if not unique_expiries:
            return []
            
        target_expiry = unique_expiries[0]
        if current_dt is not None and len(unique_expiries) > 1:
            nearest_expiry_date = pd.Timestamp(unique_expiries[0]).date()
            if hasattr(current_dt, "date"):
                current_date = current_dt.date()
            else:
                current_date = pd.to_datetime(current_dt).date()
                
            dte = (nearest_expiry_date - current_date).days
            if dte <= expiry_buffer_days:
                target_expiry = unique_expiries[1]
                
        near_opts = opts[opts['dt'] == target_expiry].copy()
        
        chain = []
        for strike in strikes:
            row_opts = near_opts[near_opts['STRIKE_PRICE'] == strike]
            if row_opts.empty: continue
            
            ce = row_opts[row_opts['OPTION_TYPE'] == 'CE']
            pe = row_opts[row_opts['OPTION_TYPE'] == 'PE']
            
            chain.append({
                "strike": float(strike),
                "ce_token": f"NFO:{int(ce.iloc[0]['SECURITY_ID'])}" if not ce.empty else "",
                "pe_token": f"NFO:{int(pe.iloc[0]['SECURITY_ID'])}" if not pe.empty else ""
            })
        return chain

    def get_atm_options(self, spot_price: float, underlying: str = "NIFTY", current_dt: Optional[datetime] = None, expiry_buffer_days: int = 1, strike_offset: int = 0) -> Dict[str, Dict]:
        if self._fno_df is None: self._fetch_fno_master()
        
        # Symbol-aware strike interval
        interval = 100 if "BANK" in underlying else (25 if "MID" in underlying else 50)
        
        # Determine ATM strike
        remainder = spot_price % interval
        if remainder >= (interval/2):
            atm_strike = spot_price - remainder + interval
        else:
            atm_strike = spot_price - remainder
            
        # Filter for index options of the underlying
        mask = (self._fno_df['INSTRUMENT_NAME'] == 'OPTIDX') & (self._fno_df['TRADING_SYMBOL'].str.startswith(underlying + "-", na=False))
        opts = self._fno_df[mask].copy()
        
        if opts.empty: return {}
        
        # Filter for nearest/next expiry based on current date
        opts['dt'] = pd.to_datetime(opts['EXPIRY_DATE'], errors='coerce')
        unique_expiries = sorted(opts['dt'].dropna().unique())
        if not unique_expiries:
            return {}
            
        target_expiry = unique_expiries[0]
        if current_dt is None:
            try:
                from zoneinfo import ZoneInfo
                IST = ZoneInfo("Asia/Kolkata")
                current_dt = datetime.now(IST)
            except Exception:
                current_dt = datetime.now()

        if len(unique_expiries) > 1:
            nearest_expiry_date = pd.Timestamp(unique_expiries[0]).date()
            if hasattr(current_dt, "date"):
                current_date = current_dt.date()
            else:
                current_date = pd.to_datetime(current_dt).date()
                
            dte = (nearest_expiry_date - current_date).days
            if dte <= expiry_buffer_days:
                target_expiry = unique_expiries[1]
                
        near_opts = opts[opts['dt'] == target_expiry].copy()
        
        # Apply strike offset for ATM/ITM selection
        ce_strike = atm_strike - (strike_offset * interval)
        pe_strike = atm_strike + (strike_offset * interval)
        
        # Extract Call (CE) at ce_strike and Put (PE) at pe_strike
        ce_rows = near_opts[(near_opts['OPTION_TYPE'] == 'CE') & (near_opts['STRIKE_PRICE'] == ce_strike)]
        pe_rows = near_opts[(near_opts['OPTION_TYPE'] == 'PE') & (near_opts['STRIKE_PRICE'] == pe_strike)]
        
        # Fallback to ATM if ITM strike is not found
        if ce_rows.empty:
            ce_rows = near_opts[(near_opts['OPTION_TYPE'] == 'CE') & (near_opts['STRIKE_PRICE'] == atm_strike)]
        if pe_rows.empty:
            pe_rows = near_opts[(near_opts['OPTION_TYPE'] == 'PE') & (near_opts['STRIKE_PRICE'] == atm_strike)]
            
        res = {}
        for _, row in ce_rows.iterrows():
            res['CE'] = {
                "token": f"NFO:{int(row['SECURITY_ID'])}",
                "symbol": row['TRADING_SYMBOL'],
                "expiry": row['EXPIRY_DATE'],
                "strike": float(row['STRIKE_PRICE'])
            }
        for _, row in pe_rows.iterrows():
            res['PE'] = {
                "token": f"NFO:{int(row['SECURITY_ID'])}",
                "symbol": row['TRADING_SYMBOL'],
                "expiry": row['EXPIRY_DATE'],
                "strike": float(row['STRIKE_PRICE'])
            }
            
        return res

if __name__ == "__main__":
    # Test
    load_dotenv()
    dm = DerivativeManager(os.getenv("INDMONEY_TOKEN"))
    try:
        fut = dm.get_current_future("NIFTY")
        print(f"Current Nifty Future Token: {fut}")
        opts = dm.get_atm_options(22600, "NIFTY")
        print(f"ATM Options: {opts}")
    except Exception as e:
        print(f"Error: {e}")

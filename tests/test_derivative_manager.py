import pandas as pd
from datetime import datetime
from services.chartedge_core.derivative_manager import DerivativeManager

def test_derivative_manager_options_expiry_and_strike_offset():
    # Initialize DM with dummy token
    dm = DerivativeManager("DUMMY")
    
    # Construct mock F&O master dataframe
    # We will test NIFTY option selection.
    # Expiry 1: May 21st, 2026
    # Expiry 2: May 28th, 2026
    # Let's say current date is May 20th, 2026 (DTE = 1) -> rollover should happen if expiry_buffer_days >= 1
    # Let's test with strike_offset = 1 (ITM Calls and Puts)
    mock_data = [
        # Expiry 1 (May 21st, 2026)
        {"INSTRUMENT_NAME": "OPTIDX", "TRADING_SYMBOL": "NIFTY-26MAY21-22000-CE", "EXPIRY_DATE": "2026-05-21", "SECURITY_ID": 1001, "OPTION_TYPE": "CE", "STRIKE_PRICE": 22000},
        {"INSTRUMENT_NAME": "OPTIDX", "TRADING_SYMBOL": "NIFTY-26MAY21-22000-PE", "EXPIRY_DATE": "2026-05-21", "SECURITY_ID": 1002, "OPTION_TYPE": "PE", "STRIKE_PRICE": 22000},
        {"INSTRUMENT_NAME": "OPTIDX", "TRADING_SYMBOL": "NIFTY-26MAY21-21950-CE", "EXPIRY_DATE": "2026-05-21", "SECURITY_ID": 1003, "OPTION_TYPE": "CE", "STRIKE_PRICE": 21950},
        {"INSTRUMENT_NAME": "OPTIDX", "TRADING_SYMBOL": "NIFTY-26MAY21-22050-PE", "EXPIRY_DATE": "2026-05-21", "SECURITY_ID": 1004, "OPTION_TYPE": "PE", "STRIKE_PRICE": 22050},
        
        # Expiry 2 (May 28th, 2026)
        {"INSTRUMENT_NAME": "OPTIDX", "TRADING_SYMBOL": "NIFTY-26MAY28-22000-CE", "EXPIRY_DATE": "2026-05-28", "SECURITY_ID": 2001, "OPTION_TYPE": "CE", "STRIKE_PRICE": 22000},
        {"INSTRUMENT_NAME": "OPTIDX", "TRADING_SYMBOL": "NIFTY-26MAY28-22000-PE", "EXPIRY_DATE": "2026-05-28", "SECURITY_ID": 2002, "OPTION_TYPE": "PE", "STRIKE_PRICE": 22000},
        {"INSTRUMENT_NAME": "OPTIDX", "TRADING_SYMBOL": "NIFTY-26MAY28-21950-CE", "EXPIRY_DATE": "2026-05-28", "SECURITY_ID": 2003, "OPTION_TYPE": "CE", "STRIKE_PRICE": 21950},
        {"INSTRUMENT_NAME": "OPTIDX", "TRADING_SYMBOL": "NIFTY-26MAY28-22050-PE", "EXPIRY_DATE": "2026-05-28", "SECURITY_ID": 2004, "OPTION_TYPE": "PE", "STRIKE_PRICE": 22050},
    ]
    dm._fno_df = pd.DataFrame(mock_data)
    
    # Scenario A: Current Date = May 18th (DTE = 3 days to May 21st).
    # expiry_buffer_days = 1 (we don't rollover)
    # strike_offset = 0 (we want ATM, which is 22000 for spot 22000)
    current_dt_a = datetime(2026, 5, 18, 10, 0)
    opts_a = dm.get_atm_options(
        spot_price=22000, 
        underlying="NIFTY", 
        current_dt=current_dt_a, 
        expiry_buffer_days=1, 
        strike_offset=0
    )
    assert opts_a["CE"]["symbol"] == "NIFTY-26MAY21-22000-CE"
    assert opts_a["PE"]["symbol"] == "NIFTY-26MAY21-22000-PE"

    # Scenario B: Current Date = May 20th (DTE = 1 day to May 21st).
    # expiry_buffer_days = 1 (we DO rollover to May 28th)
    # strike_offset = 0 (ATM)
    current_dt_b = datetime(2026, 5, 20, 10, 0)
    opts_b = dm.get_atm_options(
        spot_price=22000, 
        underlying="NIFTY", 
        current_dt=current_dt_b, 
        expiry_buffer_days=1, 
        strike_offset=0
    )
    assert opts_b["CE"]["symbol"] == "NIFTY-26MAY28-22000-CE"
    assert opts_b["PE"]["symbol"] == "NIFTY-26MAY28-22000-PE"

    # Scenario C: Current Date = May 20th (DTE = 1 day -> Rollover to May 28th).
    # strike_offset = 1 (ITM calls/puts: Call strike = 22000 - 50 = 21950; Put strike = 22000 + 50 = 22050)
    opts_c = dm.get_atm_options(
        spot_price=22000, 
        underlying="NIFTY", 
        current_dt=current_dt_b, 
        expiry_buffer_days=1, 
        strike_offset=1
    )
    assert opts_c["CE"]["symbol"] == "NIFTY-26MAY28-21950-CE"
    assert opts_c["PE"]["symbol"] == "NIFTY-26MAY28-22050-PE"

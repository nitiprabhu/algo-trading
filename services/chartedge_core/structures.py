"""Regime-aware option structure selector.

select_structure() returns a plain dict so callers in indstocks.py can
check trade=True/False and read strike_offset without importing dataclasses.
"""
from __future__ import annotations

from services.chartedge_core.regime_agent import (
    REGIME_TRENDING_BULLISH,
    REGIME_TRENDING_BEARISH,
)

_TRENDING = {REGIME_TRENDING_BULLISH, REGIME_TRENDING_BEARISH}

_IV_RANK_HIGH = 70   # above this: block naked long premium
_IV_RANK_LOW = 30    # below this: cheap premium, ideal for buying
_OFFSET_ITM = 1      # steps ITM from ATM for trend-follow entries


from typing import List, Optional
from pydantic import BaseModel

class Leg(BaseModel):
    action: str  # "BUY" or "SELL"
    option_type: str  # "CE" or "PE"
    strike_offset: int  # e.g., 0 for ATM, 1 for 1-step ITM, -1 for 1-step OTM
    ratio: int = 1  # For ratio backspreads

class OptionStructure(BaseModel):
    trade: bool
    strategy_name: str
    legs: List[Leg]
    reason: str

def select_structure(
    regime: str,
    direction: str,
    iv_rank: float,
    spot: float,
    optimal_strategy: str = "NAKED_BUY",
    strike_offset_config: int = 0,
) -> OptionStructure:
    """
    Decide which option structure to trade given market context.
    """
    if iv_rank > _IV_RANK_HIGH and optimal_strategy == "NAKED_BUY":
        return OptionStructure(
            trade=False,
            strategy_name="NONE",
            legs=[],
            reason=f"IV rank {iv_rank:.0f} > {_IV_RANK_HIGH}: naked premium expensive, skipping."
        )

    # Base strikes
    itm_offset = _OFFSET_ITM if regime in _TRENDING else strike_offset_config
    
    legs = []
    reason = f"Regime {regime}, IV {iv_rank:.0f}: Strategy {optimal_strategy}."

    if optimal_strategy == "DEBIT_SPREAD":
        # Buy ATM/ITM, Sell OTM
        legs.append(Leg(action="BUY", option_type=direction, strike_offset=itm_offset))
        # OTM offset: -1 means 1 step out of the money
        legs.append(Leg(action="SELL", option_type=direction, strike_offset=-1))
    elif optimal_strategy == "CREDIT_SPREAD":
        # Sell ATM/ITM, Buy OTM for protection
        legs.append(Leg(action="SELL", option_type=direction, strike_offset=itm_offset))
        legs.append(Leg(action="BUY", option_type=direction, strike_offset=-2))
    elif optimal_strategy == "RATIO_BACKSPREAD":
        # Sell 1 ITM, Buy 2 OTM
        legs.append(Leg(action="SELL", option_type=direction, strike_offset=1, ratio=1))
        legs.append(Leg(action="BUY", option_type=direction, strike_offset=-1, ratio=2))
    elif optimal_strategy == "IRON_CONDOR":
        # Sell OTM Call & Put, Buy further OTM Call & Put
        legs.append(Leg(action="SELL", option_type="CE", strike_offset=-1))
        legs.append(Leg(action="BUY", option_type="CE", strike_offset=-3))
        legs.append(Leg(action="SELL", option_type="PE", strike_offset=-1))
        legs.append(Leg(action="BUY", option_type="PE", strike_offset=-3))
    elif optimal_strategy == "SHORT_STRANGLE":
        # Sell OTM Call & Put
        legs.append(Leg(action="SELL", option_type="CE", strike_offset=-2))
        legs.append(Leg(action="SELL", option_type="PE", strike_offset=-2))
    else:
        # Default naked buy
        legs.append(Leg(action="BUY", option_type=direction, strike_offset=itm_offset))

    return OptionStructure(
        trade=True,
        strategy_name=optimal_strategy,
        legs=legs,
        reason=reason
    )

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Dict, List, Union
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# Fallback for StrEnum which was added in Python 3.11
try:
    from enum import StrEnum
except ImportError:
    class StrEnum(str, Enum):
        pass


class Direction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class PositionStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    QUEUED = "QUEUED"


class Candle(BaseModel):
    time: datetime
    instrument: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class IndicatorValue(BaseModel):
    value: Union[float, Dict[str, float], str]
    vote: int = Field(ge=-1, le=1)
    state: str
    weight: float = Field(ge=0)


class OptionChainRow(BaseModel):
    strike: float
    ce_token: str
    pe_token: str

class OptionChainData(BaseModel):
    pcr: float = 0.0
    max_pain: float = 0.0
    resistance_wall: float = 0.0  # Highest Call OI strike
    support_wall: float = 0.0     # Highest Put OI strike
    oi_change_pct: float = 0.0
    chain: List[OptionChainRow] = Field(default_factory=list)


class MarketContext(BaseModel):
    reliance_trend: str = "NEUTRAL"
    hdfc_bank_trend: str = "NEUTRAL"
    india_vix: float = 0.0
    gift_nifty_spread: float = 0.0
    basis: float = 0.0  # Futures - Spot


class IndicatorSnapshot(BaseModel):
    instrument: str
    timeframe: str
    candle_time: datetime
    price: float
    indicators: Dict[str, IndicatorValue]
    confluence_score: float
    higher_timeframe: Dict[str, str] = Field(default_factory=dict)
    market_context: Optional[MarketContext] = None
    options_data: Optional[OptionChainData] = None
    regime_info: Optional[Dict[str, Any]] = Field(default=None, exclude=True)


class EntryZone(BaseModel):
    low: float
    high: float


class Signal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime
    instrument: str
    signal: Direction
    confidence: int = Field(ge=0, le=100)
    entry_zone: EntryZone
    stop_loss: float
    target_1: float
    target_2: float
    risk_reward_ratio: float
    reasoning: str
    warnings: List[str] = Field(default_factory=list)
    invalidation: str
    indicator_snapshot: IndicatorSnapshot
    ai_model: str
    ai_status: str = "OK"
    strategy_name: str = "CONFLUENCE"
    option_type: Optional[str] = None
    entry_delta: Optional[float] = None  # BS delta at entry, used for SL/T1/T2 translation
    legs: List[Dict[str, Any]] = Field(default_factory=list)  # Store resolved legs


class LegExecution(BaseModel):
    instrument: str
    action: Direction  # BUY or SELL
    ratio: int
    entry_price: float
    strike: float = 0.0
    option_type: str = "CE"
    exit_price: Optional[float] = None

class PaperTrade(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    signal_id: UUID
    instrument: str  # Can be a composite name like "DEBIT_SPREAD:NIFTY-..."
    direction: Direction
    entry_price: float  # Net premium
    entry_time: datetime
    quantity: int
    underlying_entry_price: Optional[float] = None
    last_db_update: Optional[datetime] = None
    status: PositionStatus = PositionStatus.OPEN
    sl_price: float
    t1_price: float
    t2_price: float
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    invested_amount: float = 0.0
    t1_hit: bool = False
    highest_pnl_pct: float = 0.0
    costs_paid: float = 0.0
    price_source: Optional[str] = None
    strategy_name: str = "NAKED_BUY"
    legs: List[LegExecution] = Field(default_factory=list)


class DashboardSnapshot(BaseModel):
    market_time: datetime
    feed_health: str
    signals: List[Signal]
    open_positions: List[PaperTrade]
    closed_trades: List[PaperTrade]
    equity_curve: List[Dict[str, Union[float, str]]]
    market_data_history: Dict[str, List[Dict[str, Union[float, str]]]] = Field(default_factory=dict)
    latest_indicators: Dict[str, IndicatorSnapshot]
    metrics: Dict[str, Any]
    kill_switch_enabled: bool

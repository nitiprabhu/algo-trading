from __future__ import annotations

import os
from typing import Any, Optional, List, Union
from datetime import datetime
from dotenv import load_dotenv

from sqlmodel import Field, Session, SQLModel, create_engine, select, UniqueConstraint

load_dotenv()

class DynamicParameter(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("category", "key", "instrument"),)
    
    id: Optional[int] = Field(default=None, primary_key=True)
    category: str = Field(index=True)  # e.g., 'confluence', 'indicator_weights', 'risk'
    key: str = Field(index=True)       # e.g., 'buy_threshold', 'rsi', 'notional_per_trade'
    value: str                         # The actual parameter value (stored as string)
    instrument: Optional[str] = Field(default=None, index=True)  # Optional, for per-instrument overrides
    updated_at: datetime = Field(default_factory=datetime.utcnow)


print("DEBUG: database.py: Loading module")
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("DEBUG: database.py: No DATABASE_URL found, using sqlite")
    DATABASE_URL = "sqlite:///./chartedge.db"
else:
    print(f"DEBUG: database.py: DATABASE_URL found (length={len(DATABASE_URL)})")

print("DEBUG: database.py: Creating engine")
engine = create_engine(
    DATABASE_URL, 
    connect_args={"connect_timeout": 30}
)
print("DEBUG: database.py: Engine created")


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session


def get_all_parameters() -> List[DynamicParameter]:
    with Session(engine) as session:
        try:
            statement = select(DynamicParameter)
            results = session.exec(statement)
            return list(results.all())
        except Exception:
            # Table might not exist yet
            return []


def update_parameter(category: str, key: str, value: Any, instrument: Optional[str] = None):
    return batch_update_parameters([(category, key, value, instrument)])[0]

def batch_update_parameters(params_list: List[tuple[str, str, Any, Optional[str]]]):
    """Update multiple parameters in a single session for efficiency."""
    results = []
    with Session(engine) as session:
        for category, key, value, instrument in params_list:
            statement = select(DynamicParameter).where(
                DynamicParameter.category == category,
                DynamicParameter.key == key,
                DynamicParameter.instrument == instrument
            )
            param = session.exec(statement).first()
            
            val_str = str(value)
            if param:
                param.value = val_str
                param.updated_at = datetime.utcnow()
            else:
                param = DynamicParameter(
                    category=category,
                    key=key,
                    value=val_str,
                    instrument=instrument
                )
                session.add(param)
            results.append(param)
        
        session.commit()
        for p in results:
            session.refresh(p)
    return results


# ── Trade Persistence ──────────────────────────────────────────────────────────

class TradeRecord(SQLModel, table=True):
    """Persists every paper trade entry and exit to the database."""

    id: Optional[int] = Field(default=None, primary_key=True)
    trade_id: str = Field(index=True, unique=True)  # UUID from PaperTrade
    signal_id: str = Field(index=True)
    symbol: str = Field(index=True)
    direction: str  # BUY or SELL
    entry_price: float
    entry_time: datetime
    quantity: int
    sl_price: float
    t1_price: float
    t2_price: float
    status: str = "OPEN"  # OPEN or CLOSED
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    invested_amount: float = 0.0
    t1_hit: bool = False
    highest_pnl_pct: float = 0.0
    underlying_entry_price: Optional[float] = None
    trade_date: str = Field(index=True)  # YYYY-MM-DD for easy daily queries
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


def persist_trade_entry(trade) -> Optional[TradeRecord]:
    """Save a new trade entry to the database."""
    if not DATABASE_URL:
        return None
    try:
        with Session(engine) as session:
            record = TradeRecord(
                trade_id=str(trade.id),
                signal_id=str(trade.signal_id),
                symbol=trade.instrument,
                direction=trade.direction.value,
                entry_price=trade.entry_price,
                entry_time=trade.entry_time,
                quantity=trade.quantity,
                underlying_entry_price=trade.underlying_entry_price,
                sl_price=trade.sl_price,
                t1_price=trade.t1_price,
                t2_price=trade.t2_price,
                status="OPEN",
                trade_date=trade.entry_time.strftime("%Y-%m-%d"),
                invested_amount=trade.invested_amount,
                pnl_pct=0.0
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            print(f"📥 Trade persisted: {trade.direction.value} {trade.instrument} @ {trade.entry_price}")
            return record
    except Exception as e:
        print(f"⚠️ Failed to persist trade entry: {e}")
        return None


def update_trade_mtm(trade_id: str, pnl: float, pnl_pct: float, sl_price: float, t1_hit: bool, highest_pnl_pct: float):
    """Update MTM details for an open trade."""
    if not DATABASE_URL:
        return
    try:
        with Session(engine) as session:
            statement = select(TradeRecord).where(TradeRecord.trade_id == trade_id)
            record = session.exec(statement).first()
            if record:
                record.pnl = pnl
                record.pnl_pct = pnl_pct
                record.sl_price = sl_price
                record.t1_hit = t1_hit
                record.highest_pnl_pct = highest_pnl_pct
                record.updated_at = datetime.utcnow()
                session.add(record)
                session.commit()
    except Exception as e:
        print(f"⚠️ Failed to update trade MTM: {e}")

def persist_trade_exit(trade) -> Optional[TradeRecord]:
    """Update a trade record with exit details."""
    if not DATABASE_URL:
        return None
    try:
        with Session(engine) as session:
            statement = select(TradeRecord).where(TradeRecord.trade_id == str(trade.id))
            record = session.exec(statement).first()
            if not record:
                print(f"⚠️ Trade record not found for update: {trade.id}")
                return None
            record.status = "CLOSED"
            record.exit_price = trade.exit_price
            record.exit_time = trade.exit_time
            record.exit_reason = trade.exit_reason
            record.pnl = trade.pnl
            record.pnl_pct = trade.pnl_pct
            record.t1_hit = trade.t1_hit
            record.sl_price = trade.sl_price
            record.highest_pnl_pct = trade.highest_pnl_pct
            record.updated_at = datetime.utcnow()
            session.add(record)
            session.commit()
            session.refresh(record)
            print(f"📤 Trade closed: {trade.instrument} PnL={trade.pnl} ({trade.exit_reason})")
            return record
    except Exception as e:
        print(f"⚠️ Failed to persist trade exit: {e}")
        return None


def get_open_trades() -> List[TradeRecord]:
    """Fetch all trades that are currently marked as OPEN in the database."""
    if not DATABASE_URL:
        return []
    try:
        with Session(engine) as session:
            statement = select(TradeRecord).where(TradeRecord.status == "OPEN")
            results = session.exec(statement)
            return list(results.all())
    except Exception as e:
        print(f"⚠️ Failed to fetch open trades: {e}")
        return []


def get_recent_closed_trades(limit: int = 50) -> List[TradeRecord]:
    """Fetch recent closed trades from the database for the Trade Log."""
    if not DATABASE_URL:
        return []
    try:
        with Session(engine) as session:
            statement = select(TradeRecord).where(TradeRecord.status == "CLOSED").order_by(TradeRecord.exit_time.desc()).limit(limit)
            results = session.exec(statement)
            return list(results.all())
    except Exception as e:
        print(f"⚠️ Failed to fetch recent trades: {e}")
        return []

def get_trades_for_date(date_str: str) -> List[TradeRecord]:
    """Get all trades for a specific date."""
    try:
        with Session(engine) as session:
            # Assuming date_str is YYYY-MM-DD
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            statement = select(TradeRecord).where(func.date(TradeRecord.entry_time) == d)
            results = session.exec(statement)
            return list(results.all())
    except Exception as e:
        print(f"⚠️ Failed to fetch trades for date: {e}")
        return []


def get_daily_performance() -> List[dict]:
    """Get profit/loss grouped by date and symbol."""
    if not DATABASE_URL:
        return []
    try:
        with Session(engine) as session:
            # Fetch all closed trades
            statement = select(TradeRecord).where(TradeRecord.status == "CLOSED").order_by(TradeRecord.exit_time.desc())
            results = session.exec(statement).all()
            
            from collections import defaultdict
            history = defaultdict(lambda: defaultdict(float))
            
            for r in results:
                if r.exit_time:
                    # Use exit_time for daily reporting
                    date_str = r.exit_time.strftime("%Y-%m-%d")
                    history[date_str][r.symbol] += r.pnl
            
            formatted = []
            for date_str in sorted(history.keys(), reverse=True):
                day_data = {
                    "date": date_str,
                    "symbols": {s: round(p, 2) for s, p in history[date_str].items()},
                    "total": round(sum(history[date_str].values()), 2)
                }
                formatted.append(day_data)
            return formatted
    except Exception as e:
        print(f"⚠️ Failed to fetch daily performance: {e}")
        return []

def clear_all_trades():
    """Wipe all trade records from the database."""
    if not DATABASE_URL:
        pass
    
    try:
        with Session(engine) as session:
            # Delete all from TradeRecord
            from sqlalchemy import delete
            session.execute(delete(TradeRecord))
            session.commit()
            print("🧨 All trade records cleared from database.")
    except Exception as e:
        print(f"⚠️ Failed to clear trades: {e}")


# ── Positional Stocks (technical-investment, long-only) Persistence ────────────

class StockPositionRecord(SQLModel, table=True):
    """Persists positional-stocks (large-cap technical investment) positions.
    Fully separate from TradeRecord (intraday) and the options positional
    JSON log -- own table, own capital pool."""

    id: Optional[int] = Field(default=None, primary_key=True)
    position_id: str = Field(index=True, unique=True)
    symbol: str = Field(index=True)
    entry_date: str
    entry_price: float
    quantity: int
    status: str = "OPEN"  # OPEN or CLOSED
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


def persist_stock_entry(position) -> Optional[StockPositionRecord]:
    """Save a new positional-stocks BUY entry to the database."""
    if not DATABASE_URL:
        return None
    try:
        with Session(engine) as session:
            record = StockPositionRecord(
                position_id=position.id,
                symbol=position.symbol,
                entry_date=position.entry_date,
                entry_price=position.entry_price,
                quantity=position.quantity,
                status="OPEN",
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            print(f"📥 Stock position persisted: BUY {position.symbol} @ {position.entry_price}")
            return record
    except Exception as e:
        print(f"⚠️ Failed to persist stock entry: {e}")
        return None


def persist_stock_exit(position) -> Optional[StockPositionRecord]:
    """Update a positional-stocks record with SELL exit details."""
    if not DATABASE_URL:
        return None
    try:
        with Session(engine) as session:
            statement = select(StockPositionRecord).where(StockPositionRecord.position_id == position.id)
            record = session.exec(statement).first()
            if not record:
                print(f"⚠️ Stock position record not found for update: {position.id}")
                return None
            record.status = "CLOSED"
            record.exit_date = position.exit_date
            record.exit_price = position.exit_price
            record.exit_reason = position.exit_reason
            record.pnl = position.pnl
            record.pnl_pct = position.pnl_pct
            record.updated_at = datetime.utcnow()
            session.add(record)
            session.commit()
            session.refresh(record)
            print(f"📤 Stock position closed: {position.symbol} PnL={position.pnl} ({position.exit_reason})")
            return record
    except Exception as e:
        print(f"⚠️ Failed to persist stock exit: {e}")
        return None


def get_open_stock_positions() -> List[StockPositionRecord]:
    if not DATABASE_URL:
        return []
    try:
        with Session(engine) as session:
            statement = select(StockPositionRecord).where(StockPositionRecord.status == "OPEN")
            return list(session.exec(statement).all())
    except Exception as e:
        print(f"⚠️ Failed to fetch open stock positions: {e}")
        return []


def get_closed_stock_positions(limit: int = 100) -> List[StockPositionRecord]:
    if not DATABASE_URL:
        return []
    try:
        with Session(engine) as session:
            statement = (
                select(StockPositionRecord)
                .where(StockPositionRecord.status == "CLOSED")
                .order_by(StockPositionRecord.updated_at.desc())
                .limit(limit)
            )
            return list(session.exec(statement).all())
    except Exception as e:
        print(f"⚠️ Failed to fetch closed stock positions: {e}")
        return []

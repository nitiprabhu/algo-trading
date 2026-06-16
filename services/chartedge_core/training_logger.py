import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from uuid import UUID

class TrainingLogger:
    def __init__(self, log_dir: str = "logs"):
        self.root = Path(__file__).resolve().parents[2]
        self.log_path = self.root / log_dir / "training_data.jsonl"
        self.log_path.parent.mkdir(exist_ok=True)

    def _serialize(self, obj: Any) -> Any:
        if isinstance(obj, (datetime, UUID)):
            return str(obj)
        if isinstance(obj, dict):
            return {k: self._serialize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._serialize(v) for v in obj]
        if hasattr(obj, "dict"):
            return self._serialize(obj.dict())
        return obj

    def log_entry(self, trade: Any, signal: Any):
        """Logs the initial state when a trade is entered."""
        data = {
            "event": "ENTRY",
            "timestamp": datetime.now().isoformat(),
            "trade_id": str(trade.id),
            "symbol": trade.instrument,
            "direction": trade.direction.value,
            "entry_price": trade.entry_price,
            "invested_amount": trade.entry_price * trade.quantity,
            "features": {
                "indicators": self._serialize(signal.indicator_snapshot.indicators),
                "market_context": self._serialize(signal.indicator_snapshot.market_context),
                "options_data": self._serialize(signal.indicator_snapshot.options_data),
                "confluence": signal.indicator_snapshot.confluence_score,
                "reasoning": signal.reasoning,
                "confidence": signal.confidence
            }
        }
        self._write(data)

    def log_exit(self, trade: Any):
        """Logs the outcome when a trade is closed."""
        t_exit = trade.exit_time
        t_entry = trade.entry_time
        
        # Handle naive/aware mismatch
        if t_exit and t_entry:
            if t_exit.tzinfo is not None and t_entry.tzinfo is None:
                t_exit = t_exit.replace(tzinfo=None)
            elif t_exit.tzinfo is None and t_entry.tzinfo is not None:
                t_entry = t_entry.replace(tzinfo=None)
            duration = (t_exit - t_entry).total_seconds()
        else:
            duration = 0

        data = {
            "event": "EXIT",
            "timestamp": datetime.now().isoformat(),
            "trade_id": str(trade.id),
            "symbol": trade.instrument,
            "exit_price": trade.exit_price,
            "exit_reason": trade.exit_reason,
            "pnl": trade.pnl,
            "pnl_pct": trade.pnl_pct,
            "duration_seconds": duration
        }
        self._write(data)

    def log_snapshot(self, snapshot: Any):
        """Logs the full indicator snapshot for ML features tracking (even without trade)."""
        data = {
            "event": "SNAPSHOT",
            "timestamp": datetime.now().isoformat(),
            "symbol": snapshot.instrument,
            "features": {
                "indicators": self._serialize(snapshot.indicators),
                "market_context": self._serialize(snapshot.market_context),
                "options_data": self._serialize(snapshot.options_data),
                "confluence": snapshot.confluence_score,
            }
        }
        self._write(data)

    def _write(self, data: Dict):
        with open(self.log_path, "a") as f:
            f.write(json.dumps(data) + "\n")

class OptionsLogger(TrainingLogger):
    def __init__(self, log_dir: str = "logs"):
        self.root = Path(__file__).resolve().parents[2]
        self.log_path = self.root / log_dir / "training_logger_options.jsonl"
        self.log_path.parent.mkdir(exist_ok=True)

    def log_options_state(self, snapshot: Any):
        """Logs real-time options sentiment and chain state."""
        if not snapshot.options_data:
            return
            
        opt = snapshot.options_data
        data = {
            "timestamp": datetime.now().isoformat(),
            "symbol": snapshot.instrument,
            "spot_price": snapshot.price,
            "pcr": opt.pcr,
            "resistance_wall": opt.resistance_wall,
            "support_wall": opt.support_wall,
            "max_pain": opt.max_pain,
            "oi_change_pct": opt.oi_change_pct,
            # We don't log the full chain here to keep it lean, but could if needed.
        }
        self._write(data)

# Global instances
training_logger = TrainingLogger()
options_logger = OptionsLogger()

def log_realtime_trade_action(msg: str):
    root = Path(__file__).resolve().parents[2]
    realtime_dir = root / "realtime"
    realtime_dir.mkdir(exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    log_file = realtime_dir / f"trades_{today_str}.log"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(msg.strip() + "\n\n" + "="*50 + "\n\n")

def save_trade_legs_to_cache(trade_id: str, legs: list):
    root = Path(__file__).resolve().parents[2]
    realtime_dir = root / "realtime"
    realtime_dir.mkdir(exist_ok=True)
    cache_file = realtime_dir / "legs_cache.json"
    
    # Load existing cache
    cache = {}
    if cache_file.exists():
        try:
            with open(cache_file, "r") as f:
                cache = json.load(f)
        except Exception:
            pass
            
    # Serialize legs
    serialized_legs = []
    for leg in legs:
        serialized_legs.append({
            "instrument": leg.instrument,
            "action": leg.action.value,
            "ratio": leg.ratio,
            "entry_price": leg.entry_price,
            "strike": leg.strike,
            "option_type": leg.option_type,
            "exit_price": leg.exit_price
        })
        
    cache[trade_id] = serialized_legs
    with open(cache_file, "w") as f:
        json.dump(cache, f, indent=2)

def load_trade_legs_from_cache(trade_id: str) -> list | None:
    root = Path(__file__).resolve().parents[2]
    cache_file = root / "realtime" / "legs_cache.json"
    if not cache_file.exists():
        return None
    try:
        with open(cache_file, "r") as f:
            cache = json.load(f)
        legs_data = cache.get(trade_id)
        if not legs_data:
            return None
            
        from services.chartedge_core.models import LegExecution, Direction
        legs = []
        for leg in legs_data:
            legs.append(LegExecution(
                instrument=leg["instrument"],
                action=Direction.BUY if leg["action"] == "BUY" else Direction.SELL,
                ratio=leg["ratio"],
                entry_price=leg["entry_price"],
                strike=leg["strike"],
                option_type=leg["option_type"],
                exit_price=leg["exit_price"]
            ))
        return legs
    except Exception:
        return None

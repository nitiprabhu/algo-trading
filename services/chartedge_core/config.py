from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "shared" / "config.yaml"
load_dotenv(ROOT / ".env")


class Config(BaseModel):
    instruments: list[dict[str, Any]]
    data: dict[str, Any]
    market_hours: dict[str, str]
    confluence_thresholds: dict[str, float]
    indicator_weights: dict[str, dict[str, float]]
    ai: dict[str, Any]
    risk: dict[str, Any]
    expiry_map: dict[str, Any] = {}
    iv_rank: dict[str, Any] = {}
    costs: dict[str, Any] = {}
    options_structure: dict[str, Any] = {}
    futures_risk: dict[str, Any] = {}
    positional_risk: dict[str, Any] = {}

    @property
    def costs_config(self) -> dict[str, Any]:
        return self.costs

    @property
    def futures_risk_config(self) -> dict[str, Any]:
        return self.futures_risk

    @property
    def enabled_symbols(self) -> list[str]:
        return [item["symbol"] for item in self.instruments if item.get("enabled", True)]

    @property
    def trading_symbols(self) -> list[str]:
        """Symbols actively traded via the options pipeline (role=trading only)."""
        return [
            item["symbol"] for item in self.instruments
            if item.get("enabled", True) and item.get("role", "trading") == "trading"
        ]

    @property
    def monitor_symbols(self) -> list[str]:
        return [item["symbol"] for item in self.instruments if item.get("enabled", True) and item.get("role") == "monitor"]


def sync_config_to_db(config: Config):
    """Initial synchronization of YAML config to the database."""
    from services.chartedge_core.database import create_db_and_tables, batch_update_parameters
    print("DEBUG: sync_config_to_db: imports finished")
    
    # create_db_and_tables() # Already created, keeping it commented for safety
    
    params_to_sync = []
    
    # Confluence thresholds
    for key, value in config.confluence_thresholds.items():
        params_to_sync.append(("confluence_thresholds", key, value, None))
    
    # Indicator weights
    for instrument, weights in config.indicator_weights.items():
        for indicator, weight in weights.items():
            params_to_sync.append(("indicator_weights", indicator, weight, instrument))
            
    # Risk parameters
    for key, value in config.risk.items():
        if isinstance(value, (int, float)):
            params_to_sync.append(("risk", key, float(value), None))
            
    print(f"DEBUG: sync_config_to_db: batching {len(params_to_sync)} parameters")
    batch_update_parameters(params_to_sync)
    print("DEBUG: sync_config_to_db: finished")


def apply_db_overrides(config: Config):
    """Apply database parameters over the static config."""
    if os.environ.get("SKIP_DB_OVERRIDES") == "1":
        return

    from services.chartedge_core.database import get_all_parameters
    try:
        params = get_all_parameters()
        if not params:
            sync_config_to_db(config)
            return

        for p in params:
            try:
                # Dynamic casting based on category
                if p.category == "confluence_thresholds":
                    config.confluence_thresholds[p.key] = float(p.value)
                elif p.category == "indicator_weights":
                    if p.instrument and p.instrument in config.indicator_weights:
                        config.indicator_weights[p.instrument][p.key] = float(p.value)
                elif p.category == "risk":
                    val = float(p.value)
                    if val.is_integer():
                        config.risk[p.key] = int(val)
                    else:
                        config.risk[p.key] = val
                elif p.category == "ai":
                    # AI parameters can be strings (provider, model) or numbers (enabled, temperature)
                    try:
                        config.ai[p.key] = float(p.value)
                    except ValueError:
                        config.ai[p.key] = p.value
            except (ValueError, TypeError):
                # Fallback for unexpected string in numeric field
                pass
    except Exception as e:
        print(f"Warning: Failed to load config from database: {e}")


from typing import Any, Optional, Dict, List, Union


@lru_cache
def load_config(path: Union[str, Path] = DEFAULT_CONFIG_PATH) -> Config:
    print(f"DEBUG: Entering load_config with path: {path}")
    with Path(path).open("r", encoding="utf-8") as handle:
        config = Config.model_validate(yaml.safe_load(handle))
    
    # Apply database overrides if available
    apply_db_overrides(config)
    
    print(f"DEBUG: Trading symbols: {config.trading_symbols}")
    print(f"DEBUG: Monitor symbols: {config.monitor_symbols}")
    print("DEBUG: Exiting load_config")
    return config

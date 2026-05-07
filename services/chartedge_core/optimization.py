from __future__ import annotations

from typing import Dict, List, Optional
from services.chartedge_core.database import update_parameter, get_all_parameters


class ParameterOptimizer:
    """
    Helper class to allow the AI model to learn and optimize trading parameters.
    This writes back to the 'dynamicparameter' table to stabilize profit.
    """

    @staticmethod
    def optimize_confluence_thresholds(buy_threshold: float, sell_threshold: float):
        """Update global confluence thresholds based on model learning."""
        update_parameter("confluence_thresholds", "buy_threshold", buy_threshold)
        update_parameter("confluence_thresholds", "sell_threshold", sell_threshold)

    @staticmethod
    def optimize_indicator_weights(instrument: str, weights: Dict[str, float]):
        """Adjust indicator weights for a specific instrument to maximize win rate."""
        for indicator, weight in weights.items():
            update_parameter("indicator_weights", indicator, weight, instrument=instrument)

    @staticmethod
    def optimize_risk_params(params: Dict[str, float]):
        """Adjust risk parameters (e.g., confidence floor) dynamically."""
        for key, value in params.items():
            update_parameter("risk", key, value)


# Example usage for the model:
# ParameterOptimizer.optimize_confluence_thresholds(0.65, -0.65)

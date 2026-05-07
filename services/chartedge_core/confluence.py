from __future__ import annotations

from services.chartedge_core.models import Direction, IndicatorValue


def score(indicators: dict[str, IndicatorValue]) -> float:
    weighted = sum(item.vote * item.weight for item in indicators.values())
    total = sum(item.weight for item in indicators.values()) or 1
    return round(weighted / total, 4)


def consideration(confluence_score: float, buy_threshold: float, sell_threshold: float) -> Direction:
    if confluence_score >= buy_threshold:
        return Direction.BUY
    if confluence_score <= sell_threshold:
        return Direction.SELL
    return Direction.HOLD

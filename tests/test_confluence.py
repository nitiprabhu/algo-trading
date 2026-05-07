from services.chartedge_core.confluence import consideration, score
from services.chartedge_core.models import Direction, IndicatorValue


def test_weighted_score_uses_indicator_weights() -> None:
    indicators = {
        "rsi": IndicatorValue(value=61, vote=1, state="BULLISH", weight=0.75),
        "volume": IndicatorValue(value=1, vote=-1, state="BEARISH", weight=0.25),
    }

    assert score(indicators) == 0.5


def test_consideration_thresholds() -> None:
    assert consideration(0.61, 0.6, -0.6) == Direction.BUY
    assert consideration(-0.61, 0.6, -0.6) == Direction.SELL
    assert consideration(0.2, 0.6, -0.6) == Direction.HOLD

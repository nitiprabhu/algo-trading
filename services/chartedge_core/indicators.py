from __future__ import annotations

from collections.abc import Sequence
from statistics import mean, pstdev

from services.chartedge_core.models import Candle, IndicatorValue


def _closes(candles: Sequence[Candle]) -> list[float]:
    return [c.close for c in candles]


def ema(values: Sequence[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = (value * alpha) + (result * (1 - alpha))
    return result


def rsi(values: Sequence[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    # Wilder's smoothed RSI (matches TradingView/standard)
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]
    # Seed with simple average of first `period` bars
    avg_gain = mean(gains[:period]) or 0.0001
    avg_loss = mean(losses[:period]) or 0.0001
    # Wilder smooth over remaining bars
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    avg_loss = avg_loss or 0.0001
    return 100 - (100 / (1 + (avg_gain / avg_loss)))


def macd(values: Sequence[float]) -> dict[str, float]:
    if len(values) < 26:
        return {"macd": 0.0, "signal": 0.0, "hist": 0.0}
    a12 = 2 / 13
    a26 = 2 / 27
    a9 = 2 / 10
    e12 = e26 = values[0]
    macd_series: list[float] = []
    for v in values:
        e12 = v * a12 + e12 * (1 - a12)
        e26 = v * a26 + e26 * (1 - a26)
        macd_series.append(e12 - e26)
    sig = macd_series[0]
    for m in macd_series:
        sig = m * a9 + sig * (1 - a9)
    macd_line = macd_series[-1]
    return {"macd": macd_line, "signal": sig, "hist": macd_line - sig}


def atr(candles: Sequence[Candle], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    ranges = []
    for prev, cur in zip(candles[-period - 1 : -1], candles[-period:]):
        ranges.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    return mean(ranges) if ranges else 0.0


def supertrend(candles: Sequence[Candle], period: int = 7, multiplier: float = 3.0) -> dict[str, float]:
    """Proper stateful Supertrend computed over full candle series."""
    if len(candles) < period + 1:
        last = candles[-1] if candles else None
        return {"value": last.low if last else 0.0, "direction": 1.0}

    # Wilder-smoothed ATR (RMA)
    trs: list[float] = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    rma = sum(trs[:period]) / period
    atr_series: list[float] = [rma]
    for tr in trs[period:]:
        rma = (rma * (period - 1) + tr) / period
        atr_series.append(rma)

    # atr_series[i] corresponds to candles[period + i]
    direction = 1
    final_ub = final_lb = 0.0
    st_val = 0.0

    for i, atr_val in enumerate(atr_series):
        idx = period + i
        c = candles[idx]
        mid = (c.high + c.low) / 2
        basic_ub = mid + multiplier * atr_val
        basic_lb = mid - multiplier * atr_val

        if i == 0:
            final_ub = basic_ub
            final_lb = basic_lb
        else:
            prev_close = candles[idx - 1].close
            final_ub = basic_ub if basic_ub < final_ub or prev_close > final_ub else final_ub
            final_lb = basic_lb if basic_lb > final_lb or prev_close < final_lb else final_lb

        if i == 0:
            direction = 1 if c.close > final_lb else -1
        else:
            if direction == 1:
                direction = -1 if c.close < final_lb else 1
            else:
                direction = 1 if c.close > final_ub else -1

        st_val = final_lb if direction == 1 else final_ub

    return {"value": round(st_val, 2), "direction": float(direction)}


def adx(candles: Sequence[Candle], period: int = 14) -> float:
    """Wilder's ADX — trend-strength gauge. >25 strong trend, <20 chop/range."""
    if len(candles) < period * 2:
        return 0.0
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    trs: list[float] = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        up = c.high - p.high
        down = p.low - c.low
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))

    # Wilder smoothing (RMA)
    atr_s = sum(trs[:period])
    pdm_s = sum(plus_dm[:period])
    mdm_s = sum(minus_dm[:period])
    dxs: list[float] = []
    for i in range(period, len(trs)):
        atr_s = atr_s - (atr_s / period) + trs[i]
        pdm_s = pdm_s - (pdm_s / period) + plus_dm[i]
        mdm_s = mdm_s - (mdm_s / period) + minus_dm[i]
        if atr_s <= 0:
            continue
        pdi = 100 * (pdm_s / atr_s)
        mdi = 100 * (mdm_s / atr_s)
        denom = pdi + mdi
        dx = 100 * abs(pdi - mdi) / denom if denom > 0 else 0.0
        dxs.append(dx)
    if not dxs:
        return 0.0
    # ADX = Wilder-smoothed DX
    if len(dxs) < period:
        return mean(dxs)
    adx_val = mean(dxs[:period])
    for dx in dxs[period:]:
        adx_val = (adx_val * (period - 1) + dx) / period
    return adx_val


def vwap(candles: Sequence[Candle]) -> float:
    typical_x_volume = sum(((c.high + c.low + c.close) / 3) * c.volume for c in candles)
    volume = sum(c.volume for c in candles)
    if volume <= 0:
        return 0.0
    return typical_x_volume / volume


def bollinger(values: Sequence[float], period: int = 20) -> dict[str, float]:
    # Drop non-finite values: statistics.pstdev raises an opaque
    # "'float' object has no attribute 'numerator'" on NaN/inf input.
    import math
    window = [v for v in list(values[-period:]) if math.isfinite(v)]
    mid = mean(window) if window else 0.0
    dev = pstdev(window) if len(window) > 1 else 0.0
    upper = mid + (2 * dev)
    lower = mid - (2 * dev)
    pct_b = 0.5 if upper == lower else (values[-1] - lower) / (upper - lower)
    return {"upper": upper, "mid": mid, "lower": lower, "pct_b": pct_b}


def compute_snapshot_indicators(
    candles: Sequence[Candle], weights: dict[str, float]
) -> dict[str, IndicatorValue]:
    values = _closes(candles)
    last = candles[-1]
    prev = candles[-2] if len(candles) > 1 else candles[-1]

    rsi_value = rsi(values)
    # Refined RSI: Bullish above 50 (momentum), Bearish below 50. 
    # Use a small 2-point neutral buffer to avoid flip-flopping.
    rsi_vote = 1 if rsi_value > 52 else -1 if rsi_value < 48 else 0

    macd_value = macd(values)
    macd_vote = 1 if macd_value["hist"] > 0 else -1 if macd_value["hist"] < 0 else 0

    ema9, ema21, ema50, ema200 = (ema(values, p) for p in (9, 21, 50, 200))
    ema_vote = 1 if ema9 > ema21 > ema50 else -1 if ema9 < ema21 < ema50 else 0

    vwap_value = vwap(candles[-40:])
    vwap_vote = 0 if vwap_value <= 0 else 1 if last.close > vwap_value else -1 if last.close < vwap_value else 0

    atr_value = atr(candles)
    adx_value = adx(candles)
    bb_value = bollinger(values)
    st = supertrend(candles)
    st_direction = int(st["direction"])
    supertrend_vote = st_direction  # +1 bullish, -1 bearish

    volume_ma = mean([c.volume for c in candles[-20:]]) if candles else 0
    volume_vote = 1 if last.volume > volume_ma and last.close > prev.close else -1 if last.volume > volume_ma and last.close < prev.close else 0

    def get_w(key: str, default: float) -> float:
        return weights.get(key, default)

    return {
        "rsi": IndicatorValue(value=round(rsi_value, 2), vote=rsi_vote, state=_state(rsi_vote), weight=get_w("rsi", 0.15)),
        "macd": IndicatorValue(value={k: round(v, 2) for k, v in macd_value.items()}, vote=macd_vote, state=_state(macd_vote), weight=get_w("macd", 0.15)),
        "ema_ribbon": IndicatorValue(value={"ema9": round(ema9, 2), "ema21": round(ema21, 2), "ema50": round(ema50, 2), "ema200": round(ema200, 2)}, vote=ema_vote, state=_state(ema_vote), weight=get_w("ema_ribbon", 0.15)),
        "vwap": IndicatorValue(value=round(vwap_value, 2), vote=vwap_vote, state=_state(vwap_vote), weight=get_w("vwap", 0.20)),
        "supertrend": IndicatorValue(value=st["value"], vote=supertrend_vote, state=_state(supertrend_vote), weight=get_w("supertrend", 0.25)),
        "volume": IndicatorValue(value={"current": last.volume, "ma20": round(volume_ma, 2)}, vote=volume_vote, state=_state(volume_vote), weight=get_w("volume", 0.10)),
        "atr": IndicatorValue(value=round(atr_value, 2), vote=0, state="REFERENCE", weight=0),
        "adx": IndicatorValue(value=round(adx_value, 2), vote=0, state="TRENDING" if adx_value >= 20 else "CHOPPY", weight=0),
        "bollinger": IndicatorValue(value={k: round(v, 4) for k, v in bb_value.items()}, vote=0, state="REFERENCE", weight=0),
    }


def _state(vote: int) -> str:
    return "BULLISH" if vote > 0 else "BEARISH" if vote < 0 else "NEUTRAL"

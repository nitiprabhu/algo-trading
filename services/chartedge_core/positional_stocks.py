"""
positional_stocks.py
---------------------
Technical-investment module for large-cap stocks: long-only, daily-swing
BUY/SELL signals driven by weighted technical confluence (RSI, MACD, EMA,
Supertrend, ADX, Golden Cross, Donchian breakout, Bollinger squeeze, MA
pullback, Cup & Handle). Fully isolated from the intraday options engine
and the weekly options positional module (positional_trading.py) -- own
capital pool, own DB table, own runtime loop.

This is "technical investment", not trading:
  - A position can only ever be opened by a BUY signal (maybe_enter). There
    is no SELL-to-open path -- no shorting, ever.
  - The only way a SELL signal acts is to close an existing open BUY
    position (check_exits). A SELL/bearish signal while flat is a no-op.

Signal generation (compute_stock_signal) reuses services.chartedge_core.
indicators + confluence -- the same weighted-vote model used for
NIFTY/BANKNIFTY -- extended with positional-specific patterns that need
longer daily history (Golden Cross, 52-week high/low, Cup & Handle). Those
extra patterns are gated: excluded from the vote (vote=0, weight=0) until
a symbol has accumulated enough daily bars, since indstocks.py only seeds
~4 trading days of 1-minute candles at startup.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime
from statistics import mean
from typing import Optional, Sequence
from uuid import uuid4

from services.chartedge_core.models import Candle, IndicatorValue
from services.chartedge_core.indicators import rsi, macd, ema, supertrend, adx, bollinger, _closes
from services.chartedge_core import confluence

GOLDEN_CROSS_MIN_BARS = 200
CUP_HANDLE_MIN_BARS = 60
FIFTY_TWO_WEEK_MIN_BARS = 200
DONCHIAN_WINDOW = 20


@dataclass
class StockPosition:
    id: str
    symbol: str
    entry_date: str
    entry_price: float
    quantity: int
    status: str = "OPEN"  # OPEN or CLOSED
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    peak_pnl_pct: float = 0.0  # tracks best PnL% seen, drives the trailing stop

    def to_dict(self) -> dict:
        return asdict(self)


def daily_candles_from_1m(candles: Sequence[Candle]) -> list[Candle]:
    """Resample 1-minute candles into daily OHLCV bars. indstocks.py only
    fetches 1m data -- there is no separate daily feed -- so every daily
    indicator in this module works off this resample."""
    if not candles:
        return []
    buckets: dict[date, list[Candle]] = {}
    for c in candles:
        buckets.setdefault(c.time.date(), []).append(c)
    daily: list[Candle] = []
    for day in sorted(buckets):
        day_candles = buckets[day]
        daily.append(Candle(
            time=datetime.combine(day, datetime.min.time()),
            instrument=day_candles[0].instrument,
            timeframe="1D",
            open=day_candles[0].open,
            high=max(c.high for c in day_candles),
            low=min(c.low for c in day_candles),
            close=day_candles[-1].close,
            volume=sum(c.volume for c in day_candles),
        ))
    return daily


def _golden_cross_vote(closes: list[float]) -> int:
    if len(closes) < GOLDEN_CROSS_MIN_BARS:
        return 0
    sma50 = mean(closes[-50:])
    sma200 = mean(closes[-200:])
    prev_sma50 = mean(closes[-51:-1])
    prev_sma200 = mean(closes[-201:-1])
    if prev_sma50 <= prev_sma200 and sma50 > sma200:
        return 1  # golden cross just formed
    if prev_sma50 >= prev_sma200 and sma50 < sma200:
        return -1  # death cross just formed
    return 1 if sma50 > sma200 else -1


def _fifty_two_week_vote(candles: list[Candle]) -> int:
    if len(candles) < FIFTY_TWO_WEEK_MIN_BARS:
        return 0
    window = candles[-FIFTY_TWO_WEEK_MIN_BARS:]
    high_52w = max(c.high for c in window)
    low_52w = min(c.low for c in window)
    last_close = candles[-1].close
    if high_52w <= low_52w:
        return 0
    if last_close >= high_52w * 0.98:
        return 1
    if last_close <= low_52w * 1.02:
        return -1
    return 0


def _donchian_breakout_vote(candles: list[Candle]) -> int:
    if len(candles) < DONCHIAN_WINDOW + 1:
        return 0
    prior_window = candles[-DONCHIAN_WINDOW - 1:-1]
    donchian_high = max(c.high for c in prior_window)
    donchian_low = min(c.low for c in prior_window)
    last = candles[-1]
    vol_ma = mean(c.volume for c in prior_window) or 1
    if last.close > donchian_high and last.volume > vol_ma:
        return 1
    if last.close < donchian_low and last.volume > vol_ma:
        return -1
    return 0


def _bollinger_squeeze_vote(closes: list[float]) -> int:
    if len(closes) < 40:
        return 0
    bands_now = bollinger(closes)
    width_now = bands_now["upper"] - bands_now["lower"]
    widths = []
    for i in range(20, len(closes)):
        b = bollinger(closes[: i + 1])
        widths.append(b["upper"] - b["lower"])
    if not widths:
        return 0
    recent_min_width = min(widths[-20:])
    was_squeezed = recent_min_width <= mean(widths) * 0.6
    if not was_squeezed:
        return 0
    prev_close = closes[-2]
    return 1 if closes[-1] > prev_close and width_now > recent_min_width else (
        -1 if closes[-1] < prev_close else 0
    )


def _ma_pullback_bounce_vote(closes: list[float]) -> int:
    if len(closes) < 55:
        return 0
    ema50 = ema(closes, 50)
    ema20 = ema(closes[:-1], 20)
    last_close = closes[-1]
    prev_close = closes[-2]
    uptrend = last_close > ema50
    pulled_back = prev_close <= ema20 * 1.01
    bounced = last_close > prev_close and last_close > ema20
    if uptrend and pulled_back and bounced:
        return 1
    return 0


def detect_cup_and_handle(candles: list[Candle]) -> int:
    """Relaxed cup & handle scan on daily closes: prior swing high -> ~15-35%
    trough -> recovery back near the high (the cup) -> shallow ~5-15%
    pullback (the handle) -> breakout above the handle high on volume.
    Needs CUP_HANDLE_MIN_BARS+ daily bars; returns 0 (no vote) until then."""
    if len(candles) < CUP_HANDLE_MIN_BARS:
        return 0
    closes = _closes(candles)
    window = candles[-CUP_HANDLE_MIN_BARS:]
    window_closes = closes[-CUP_HANDLE_MIN_BARS:]

    peak_idx = max(range(len(window_closes) - 15), key=lambda i: window_closes[i])
    peak_price = window_closes[peak_idx]
    post_peak = window_closes[peak_idx:]
    if len(post_peak) < 15:
        return 0

    trough_idx_rel = min(range(len(post_peak)), key=lambda i: post_peak[i])
    trough_price = post_peak[trough_idx_rel]
    decline_pct = (peak_price - trough_price) / peak_price * 100 if peak_price else 0
    if not (15 <= decline_pct <= 35):
        return 0

    recovery = post_peak[trough_idx_rel:]
    if len(recovery) < 8:
        return 0
    recovered_near_peak = max(recovery) >= peak_price * 0.95
    if not recovered_near_peak:
        return 0

    handle = recovery[-8:]
    handle_high = max(handle)
    handle_low = min(handle)
    handle_pullback_pct = (handle_high - handle_low) / handle_high * 100 if handle_high else 0
    if not (5 <= handle_pullback_pct <= 15):
        return 0

    last = window[-1]
    vol_ma = mean(c.volume for c in window[-20:]) or 1
    if last.close > handle_high and last.volume > vol_ma:
        return 1
    return 0


def compute_stock_signal(daily: list[Candle], weights: dict[str, float]) -> tuple[float, dict[str, IndicatorValue]]:
    """Weighted confluence score + vote breakdown for one stock, off daily
    candles only. BUY-only module: SELL votes here are used exclusively to
    close an existing position, never to open a short."""
    closes = _closes(daily)

    rsi_v = rsi(closes)
    rsi_vote = 1 if rsi_v > 52 else -1 if rsi_v < 48 else 0

    macd_v = macd(closes)
    macd_vote = 1 if macd_v["hist"] > 0 else -1 if macd_v["hist"] < 0 else 0

    ema20, ema50 = ema(closes, 20), ema(closes, 50)
    ema_vote = 1 if ema20 > ema50 else -1 if ema20 < ema50 else 0

    st = supertrend(daily)
    st_vote = int(st["direction"])

    adx_v = adx(daily)

    golden_cross_vote = _golden_cross_vote(closes)
    fifty_two_wk_vote = _fifty_two_week_vote(daily)
    donchian_vote = _donchian_breakout_vote(daily)
    squeeze_vote = _bollinger_squeeze_vote(closes)
    pullback_vote = _ma_pullback_bounce_vote(closes)
    cup_handle_vote = detect_cup_and_handle(daily)

    def get_w(key: str, default: float) -> float:
        return weights.get(key, default)

    def state(vote: int) -> str:
        return "BULLISH" if vote > 0 else "BEARISH" if vote < 0 else "NEUTRAL"

    matured = len(daily) >= GOLDEN_CROSS_MIN_BARS

    indicators = {
        "rsi": IndicatorValue(value=round(rsi_v, 2), vote=rsi_vote, state=state(rsi_vote), weight=get_w("rsi", 0.15)),
        "macd": IndicatorValue(value={k: round(v, 2) for k, v in macd_v.items()}, vote=macd_vote, state=state(macd_vote), weight=get_w("macd", 0.15)),
        "ema_ribbon": IndicatorValue(value={"ema20": round(ema20, 2), "ema50": round(ema50, 2)}, vote=ema_vote, state=state(ema_vote), weight=get_w("ema_ribbon", 0.15)),
        "supertrend": IndicatorValue(value=st["value"], vote=st_vote, state=state(st_vote), weight=get_w("supertrend", 0.15)),
        "golden_cross": IndicatorValue(value="50v200_SMA", vote=golden_cross_vote, state=state(golden_cross_vote), weight=get_w("golden_cross", 0.15) if matured else 0.0),
        "fifty_two_week": IndicatorValue(value="range_proximity", vote=fifty_two_wk_vote, state=state(fifty_two_wk_vote), weight=get_w("fifty_two_week", 0.10) if matured else 0.0),
        "donchian_breakout": IndicatorValue(value=f"{DONCHIAN_WINDOW}D_high_low", vote=donchian_vote, state=state(donchian_vote), weight=get_w("donchian_breakout", 0.10)),
        "bollinger_squeeze": IndicatorValue(value="squeeze_breakout", vote=squeeze_vote, state=state(squeeze_vote), weight=get_w("bollinger_squeeze", 0.05)),
        "ma_pullback": IndicatorValue(value="pullback_bounce", vote=pullback_vote, state=state(pullback_vote), weight=get_w("ma_pullback", 0.05)),
        "cup_and_handle": IndicatorValue(value="cup_handle_breakout", vote=cup_handle_vote, state=state(cup_handle_vote), weight=get_w("cup_and_handle", 0.10) if len(daily) >= CUP_HANDLE_MIN_BARS else 0.0),
        "adx": IndicatorValue(value=round(adx_v, 2), vote=0, state="REFERENCE", weight=0),
    }
    score = confluence.score(indicators)
    return score, indicators


class PositionalStocksEngine:
    """Long-only technical-investment engine. Owns its own capital and
    positions; persisted to the real DB (StockPositionRecord), not JSON.
    Multiple concurrent positions across different stocks are supported,
    unlike the single-open-trade weekly options module."""

    def __init__(self, capital: float = 100000.0, max_positions: int = 4,
                 stop_loss_pct: float = 6.0, target_pct: float = 12.0, pool: str = "largecap",
                 confidence_sizing: bool = False):
        from services.chartedge_core.database import create_db_and_tables
        create_db_and_tables()  # idempotent CREATE TABLE IF NOT EXISTS; ensures StockPositionRecord exists
        self.capital = capital
        self.max_positions = max_positions
        self.stop_loss_pct = stop_loss_pct
        self.target_pct = target_pct
        self.pool = pool  # separates capital pools (e.g. "largecap" vs "midcap") sharing one DB table
        # confidence_sizing=True: position size scales with confluence score strength
        # instead of a fixed capital/max_positions slot per name -- stronger signals get
        # a bigger allocation, capped by remaining deployable capital in the pool.
        self.confidence_sizing = confidence_sizing
        self.open_positions: dict[str, StockPosition] = {}
        self.closed_positions: list[StockPosition] = []
        self._load()

    def _load(self) -> None:
        from services.chartedge_core.database import get_open_stock_positions, get_closed_stock_positions
        for rec in get_open_stock_positions(pool=self.pool):
            pos = StockPosition(
                id=rec.position_id, symbol=rec.symbol, entry_date=rec.entry_date,
                entry_price=rec.entry_price, quantity=rec.quantity, status=rec.status,
            )
            self.open_positions[rec.symbol] = pos
        for rec in get_closed_stock_positions(pool=self.pool):
            self.closed_positions.append(StockPosition(
                id=rec.position_id, symbol=rec.symbol, entry_date=rec.entry_date,
                entry_price=rec.entry_price, quantity=rec.quantity, status=rec.status,
                exit_date=rec.exit_date, exit_price=rec.exit_price,
                exit_reason=rec.exit_reason, pnl=rec.pnl, pnl_pct=rec.pnl_pct,
            ))

    def slot_capital(self) -> float:
        return self.capital / self.max_positions

    def deployed_capital(self) -> float:
        return sum(p.entry_price * p.quantity for p in self.open_positions.values())

    def _sized_allocation(self, price: float, score: float, buy_threshold: float) -> float:
        """Confidence-weighted slot size: 1x base slot at the buy_threshold,
        scaling up to 2x base slot as score approaches its max, capped by
        whatever capital is still undeployed in the pool. Score is always
        >= buy_threshold here (gated by the caller)."""
        available = self.capital - self.deployed_capital()
        if not self.confidence_sizing:
            return min(self.slot_capital(), available)
        confidence_ratio = score / buy_threshold if buy_threshold > 0 else 1.0
        multiplier = min(max(confidence_ratio, 1.0), 2.0)
        desired = self.slot_capital() * multiplier
        return min(desired, available)

    def maybe_enter(self, symbol: str, today: date, price: float, score: float,
                     buy_threshold: float, adx_value: float, min_adx: float = 25.0,
                     trend_confirmed: bool = True) -> Optional[StockPosition]:
        """The ONLY way a position opens. Always a BUY -- there is no
        equivalent maybe_short(). trend_confirmed is a hard AND-gate (both
        EMA ribbon and Supertrend independently bullish) on top of the
        weighted confluence score -- backtested to matter: without it,
        12mo/4-stock return was -1.3%; with it (+ wider universe), +9.8%."""
        if symbol in self.open_positions:
            return None
        if len(self.open_positions) >= self.max_positions:
            return None
        if score < buy_threshold:
            return None
        if adx_value < min_adx:
            return None
        if not trend_confirmed:
            return None

        alloc = self._sized_allocation(price, score, buy_threshold)
        quantity = int(alloc // price) if price > 0 else 0
        if quantity <= 0:
            return None

        from services.chartedge_core.database import persist_stock_entry
        position = StockPosition(
            id=str(uuid4()), symbol=symbol, entry_date=today.strftime("%Y-%m-%d"),
            entry_price=round(price, 2), quantity=quantity,
        )
        self.open_positions[symbol] = position
        persist_stock_entry(symbol, position.entry_date, position.entry_price, quantity, pool=self.pool)
        return position

    def check_exit(self, symbol: str, today: date, price: float, score: float,
                    sell_threshold: float, trail_arm_pct: float = 3.0,
                    trail_keep_frac: float = 0.5) -> Optional[StockPosition]:
        """Closes an existing BUY position only. Never opens a short.
        Once a position's peak gain reaches trail_arm_pct, the position
        stops trusting weak SELL_SIGNAL exits and switches to a trailing
        stop that locks in trail_keep_frac of the peak gain -- lets winners
        run instead of bailing at the first bearish confluence flip."""
        pos = self.open_positions.get(symbol)
        if pos is None:
            return None

        pnl_pct = (price - pos.entry_price) / pos.entry_price * 100 if pos.entry_price else 0
        pos.peak_pnl_pct = max(pos.peak_pnl_pct, pnl_pct)
        trail_armed = pos.peak_pnl_pct >= trail_arm_pct

        reason = None
        if trail_armed:
            trail_floor = pos.peak_pnl_pct * trail_keep_frac
            if pnl_pct <= trail_floor:
                reason = "TRAILING_STOP"
            elif pnl_pct >= self.target_pct:
                reason = "TARGET"
        else:
            if pnl_pct <= -self.stop_loss_pct:
                reason = "STOP_LOSS"
            elif pnl_pct >= self.target_pct:
                reason = "TARGET"
            elif score <= sell_threshold:
                reason = "SELL_SIGNAL"

        if reason is None:
            return None

        return self._finalize_close(symbol, today, price, reason)

    def _finalize_close(self, symbol: str, today: date, price: float, reason: str) -> StockPosition:
        pos = self.open_positions[symbol]
        pnl_pct = (price - pos.entry_price) / pos.entry_price * 100 if pos.entry_price else 0
        pos.peak_pnl_pct = max(pos.peak_pnl_pct, pnl_pct)
        pos.exit_date = today.strftime("%Y-%m-%d")
        pos.exit_price = round(price, 2)
        pos.exit_reason = reason
        pos.pnl = round((price - pos.entry_price) * pos.quantity, 2)
        pos.pnl_pct = round(pnl_pct, 2)
        pos.status = "CLOSED"

        from services.chartedge_core.database import persist_stock_exit
        persist_stock_exit(pos.id, pos.exit_date, pos.exit_price, pos.exit_reason, pos.pnl, pos.pnl_pct, pos.peak_pnl_pct)

        self.closed_positions.append(pos)
        del self.open_positions[symbol]
        return pos

    def maybe_rotate_and_enter(self, symbol: str, today: date, price: float, score: float,
                                buy_threshold: float, open_scores: dict[str, float],
                                open_prices: dict[str, float], trail_arm_pct: float = 3.0,
                                rotation_margin: float = 0.15) -> Optional[tuple[StockPosition, StockPosition]]:
        """Portfolio-manager-style capital rotation. Call this only after
        maybe_enter() has already returned None purely because the pool is
        full (max_positions reached or capital fully deployed) -- the caller
        is responsible for having already confirmed score/ADX/trend_gate all
        qualify. Finds the weakest currently open position (lowest today's
        confluence score, from open_scores) and, only if the new signal
        beats it by rotation_margin AND that position hasn't already proven
        itself as a runner (peak gain still below trail_arm_pct, so no
        trailing-stop profit lock is active), force-sells it and reinvests
        the freed capital into the new signal. Returns (closed_position,
        new_position) on a rotation, or None if no rotation happened."""
        if symbol in self.open_positions or not self.open_positions:
            return None

        candidates = [
            (sym, open_scores[sym]) for sym in self.open_positions
            if sym in open_scores and self.open_positions[sym].peak_pnl_pct < trail_arm_pct
        ]
        if not candidates:
            return None

        weakest_symbol, weakest_score = min(candidates, key=lambda x: x[1])
        if score - weakest_score < rotation_margin:
            return None
        if weakest_symbol not in open_prices:
            return None

        closed = self._finalize_close(weakest_symbol, today, open_prices[weakest_symbol], "ROTATED_OUT")
        opened = self.maybe_enter(symbol, today, price, score, buy_threshold, adx_value=999.0,
                                   min_adx=0.0, trend_confirmed=True)
        if opened is None:
            return None  # freed capital but couldn't size an entry (e.g. price > freed capital) -- position stays closed
        return closed, opened

    def metrics(self) -> dict:
        n = len(self.closed_positions)
        wins = sum(1 for p in self.closed_positions if p.pnl > 0)
        total = sum(p.pnl for p in self.closed_positions)
        return {
            "open_count": len(self.open_positions),
            "closed_count": n,
            "wins": wins,
            "win_pct": round(wins / n * 100, 1) if n else 0.0,
            "net_pnl": round(total, 2),
            "capital": self.capital,
            "return_pct": round(total / self.capital * 100, 2) if self.capital else 0.0,
        }

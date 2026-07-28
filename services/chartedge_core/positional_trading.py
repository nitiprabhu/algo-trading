"""
positional_trading.py
----------------------
Standalone weekly NIFTY options module. Fully isolated from the intraday
engine (strategies.py / paper_trading.py / futures_trader.py) — separate
capital pool, separate position tracking, separate persistence. Importing or
running this module has zero effect on intraday trading.

Three strategy variants, all validated on 2 years of real NSE bhavcopy
settlement prices (Jul 2024-Jul 2026), selectable via
shared/config.yaml `positional_risk.strategy`:

  "condor"        Iron Condor (short strangle + protective wings). Defined
                   risk, no VIX/trend gate (gate tested overfit, see
                   memory/options_strategy_findings.md). 106 cycles, 75% win,
                   +Rs 71,677/2yr. DEFAULT — matches earlier explicit
                   "can't bear much" stop-loss preference (bounded max loss).
  "straddle"      Short ATM straddle, no wings. Highest raw profit
                   (+Rs 159,964/2yr, 106 cycles, 64% win) but UNDEFINED RISK
                   on both legs and the largest single-month loss seen
                   (-Rs 24,881 in May 2026). Higher-risk opt-in.
  "credit_spread" Single-side (put-spread if 5d uptrend else call-spread),
                   defined risk, lowest variance. 106 cycles, 83% win,
                   +Rs 40,886/2yr — weaker return but flattest equity curve.
  "iv_gated_straddle"  Short ATM straddle, but ONLY enters when straddle
                   premium / spot >= 1.3% (rich-IV weeks only; skips ~63%
                   of cycles). 150% stop (vs 110% for the other variants --
                   this is the value tested in the 2yr study, wider because
                   the gate already filters out the weeks that need a tight
                   stop). UNDEFINED RISK like "straddle". 2yr backtest: 38
                   filtered cycles (of 103), 74% win, +Rs 298,295, avg
                   +Rs 7,850/cycle -- best Sharpe (1.10) of all 4 variants,
                   and the only one that stayed net positive through the
                   2026 H1 bear chop (+Rs 60,746) where the unfiltered
                   straddle and credit_spread both struggled. Lean-premium
                   weeks (excluded by the gate) were net losers as a group
                   in the study (-Rs 44,450), confirming the gate carries
                   the edge, not the straddle itself.
                   See memory/bhavcopy_pattern_study_2026_07.md.

Flow (same for all variants):
  1. strategy.is_entry_day() — first trading day after prior cycle's expiry.
  2. strategy.size_legs() — resolves strikes for the variant.
  3. PositionalTradingEngine.maybe_enter() — resolves premiums via caller-
     supplied chain dict, opens position, own capital accounting.
  4. PositionalTradingEngine.mark_to_market() — exits at profit-take / stop /
     expiry, whichever comes first.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, date, timedelta
from typing import Callable, Optional
from uuid import uuid4

STEP = 50            # NIFTY strike interval
LOT_SIZE = 65         # current NIFTY lot size (SEBI revised 25->75->65; confirmed via
                      # Upstox /option/contract 2026-07-28 -- revise if NSE changes it again)
SHORT_SIGMA_MULT = 0.85
WING_SIGMA_MULT = 1.30
PROFIT_TAKE_FRAC = 0.55   # exit once 55% of credit is captured
STOP_CREDIT_MULT = 1.1    # exit if debit-to-close >= 1.1x credit received
                          # (tested 2.2/1.5/1.3/1.1 on 2yr real bhavcopy data: 2.2-1.3 all
                          # share the same -16,159 worst-case single-trade loss -- tightening
                          # in that range only lowers win rate, doesn't cap the tail. 1.1 is
                          # where the stop actually triggers a day earlier and halves the
                          # worst-case to -7,901, at a cost of ~8% total 2yr profit. Chosen
                          # deliberately for tighter per-trade risk over maximum return.)
NIFTY_WEEKLY_EXPIRY_WEEKDAY = 1  # Tuesday (0=Mon) — see shared/config.yaml expiry_map


@dataclass
class Leg:
    strike: float
    option_type: str   # "CE" or "PE"
    side: str          # "SHORT" or "LONG"


@dataclass
class PositionalTrade:
    id: str
    strategy: str
    entry_date: str
    expiry: str
    spot_at_entry: float
    vix_at_entry: float
    legs: list           # list[Leg]
    credit: float
    quantity: int = LOT_SIZE
    status: str = "OPEN"
    exit_date: Optional[str] = None
    debit: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def mtm(self, chain_premiums: dict[float, dict[str, float]]) -> float:
        """Net debit to close all legs at current premiums (buy back shorts, sell longs)."""
        debit = 0.0
        for leg in self.legs:
            leg_obj = leg if isinstance(leg, Leg) else Leg(**leg)
            px = chain_premiums[leg_obj.strike][leg_obj.option_type]
            debit += px if leg_obj.side == "SHORT" else -px
        return debit


def _strike_set(legs: list[Leg]) -> set[float]:
    return {leg.strike for leg in legs}


class WeeklyCondorStrategy:
    name = "condor"
    profit_take_frac = PROFIT_TAKE_FRAC
    stop_credit_mult = STOP_CREDIT_MULT

    def should_enter(self, spot: float, credit: float) -> bool:
        """Extra gate checked after credit is known, before opening the trade.
        Default: always enter (existing variants are unaffected)."""
        return True

    def next_expiry(self, from_date: date) -> date:
        days_ahead = (NIFTY_WEEKLY_EXPIRY_WEEKDAY - from_date.weekday()) % 7
        return from_date + timedelta(days=days_ahead)

    def is_entry_day(self, today: date, last_expiry: Optional[date]) -> bool:
        if last_expiry is None:
            return True
        return today > last_expiry

    def size_legs(self, spot: float, vix: float, dte_days: int, trend_pct: float = 0.0) -> list[Leg]:
        dte_days = max(dte_days, 1)
        sigma = spot * (vix / 100.0) * math.sqrt(dte_days / 365.0)
        short_off = max(round(sigma * SHORT_SIGMA_MULT / STEP) * STEP, 2 * STEP)
        wing_off = max(round(sigma * WING_SIGMA_MULT / STEP) * STEP, short_off + 2 * STEP)
        atm = round(spot / STEP) * STEP
        return [
            Leg(atm - wing_off, "PE", "LONG"),
            Leg(atm - short_off, "PE", "SHORT"),
            Leg(atm + short_off, "CE", "SHORT"),
            Leg(atm + wing_off, "CE", "LONG"),
        ]


class WeeklyStraddleStrategy(WeeklyCondorStrategy):
    """Short ATM straddle -- no wings, undefined risk. Highest raw profit in
    backtest but largest single-month loss (-24,881 May 2026); opt-in only."""
    name = "straddle"

    def size_legs(self, spot: float, vix: float, dte_days: int, trend_pct: float = 0.0) -> list[Leg]:
        atm = round(spot / STEP) * STEP
        return [
            Leg(atm, "CE", "SHORT"),
            Leg(atm, "PE", "SHORT"),
        ]


class WeeklyCreditSpreadStrategy(WeeklyCondorStrategy):
    """Single-side defined-risk spread: sell put-spread if 5d uptrend,
    call-spread if downtrend. Lowest variance of the 3 variants."""
    name = "credit_spread"

    def size_legs(self, spot: float, vix: float, dte_days: int, trend_pct: float = 0.0) -> list[Leg]:
        dte_days = max(dte_days, 1)
        sigma = spot * (vix / 100.0) * math.sqrt(dte_days / 365.0)
        short_off = max(round(sigma * SHORT_SIGMA_MULT / STEP) * STEP, 2 * STEP)
        wing_off = max(round(sigma * WING_SIGMA_MULT / STEP) * STEP, short_off + 2 * STEP)
        atm = round(spot / STEP) * STEP
        if trend_pct >= 0:
            return [Leg(atm - wing_off, "PE", "LONG"), Leg(atm - short_off, "PE", "SHORT")]
        return [Leg(atm + short_off, "CE", "SHORT"), Leg(atm + wing_off, "CE", "LONG")]


class IVGatedStraddleStrategy(WeeklyStraddleStrategy):
    """Short ATM straddle, entered only when the straddle premium is rich
    relative to spot (implied weekly move >= IV_GATE_PCT). 2yr study found
    the variance-risk-premium edge concentrates almost entirely in these
    weeks; lean-premium weeks are net losers as a group. Wider 150% stop --
    tighter stops don't cap the tail in this variant, they just cut into the
    weeks the gate was designed to keep. See memory/bhavcopy_pattern_study_2026_07.md."""
    name = "iv_gated_straddle"
    IV_GATE_PCT = 0.013          # straddle credit / spot must be >= 1.3%
    stop_credit_mult = 1.5       # vs 1.1 for the other variants
    profit_take_frac = 1.0       # effectively disabled -- study held to expiry/stop only

    def should_enter(self, spot: float, credit: float) -> bool:
        return spot > 0 and (credit / spot) >= self.IV_GATE_PCT


STRATEGIES = {
    "condor": WeeklyCondorStrategy,
    "straddle": WeeklyStraddleStrategy,
    "credit_spread": WeeklyCreditSpreadStrategy,
    "iv_gated_straddle": IVGatedStraddleStrategy,
}


class PositionalTradingEngine:
    """
    Owns its own capital, positions, and trade log. Does not touch
    PaperTradingEngine, FuturesTradingEngine, or their DB tables.
    Persists to PositionalTradeRecord table (30-day TTL after close).
    """

    def __init__(self, capital: float = 100000.0, strategy_name: str = "condor", is_backtesting: bool = False):
        if strategy_name not in STRATEGIES:
            raise ValueError(f"Unknown positional strategy: {strategy_name!r}, expected one of {list(STRATEGIES)}")
        self.capital = capital
        self.strategy_name = strategy_name
        self.strategy = STRATEGIES[strategy_name]()
        self.open_trade: Optional[PositionalTrade] = None
        self.closed_trades: list[PositionalTrade] = []
        # Backtesting must never read or write the live prod DB -- an ad-hoc
        # backtest script sharing DATABASE_URL with the live positional_risk
        # condor would otherwise inject synthetic trades into the real table.
        self.is_backtesting = is_backtesting
        if not self.is_backtesting:
            self._load()

    def _load(self) -> None:
        from services.chartedge_core.database import get_open_positional_trades, get_closed_positional_trades, create_db_and_tables
        create_db_and_tables()  # idempotent

        # Scoped to this engine's own strategy -- required when multiple
        # engines run in parallel (positional_risk.mode: parallel), otherwise
        # each engine would load another strategy's open/closed trades.
        open_records = get_open_positional_trades(strategy=self.strategy_name)
        if open_records:
            rec = open_records[0]  # should be at most 1 open trade per strategy
            legs_data = json.loads(rec.legs_json)
            self.open_trade = PositionalTrade(
                id=rec.trade_id, strategy=rec.strategy, entry_date=rec.entry_date,
                expiry=rec.expiry, spot_at_entry=rec.spot_at_entry, vix_at_entry=rec.vix_at_entry,
                legs=[Leg(**leg) for leg in legs_data], credit=rec.credit, quantity=rec.quantity, status=rec.status
            )

        # Load closed trades
        for rec in get_closed_positional_trades(limit=1000, strategy=self.strategy_name):
            legs_data = json.loads(rec.legs_json)
            self.closed_trades.append(PositionalTrade(
                id=rec.trade_id, strategy=rec.strategy, entry_date=rec.entry_date,
                expiry=rec.expiry, spot_at_entry=rec.spot_at_entry, vix_at_entry=rec.vix_at_entry,
                legs=[Leg(**leg) for leg in legs_data], credit=rec.credit, quantity=rec.quantity,
                status=rec.status, exit_date=rec.exit_date, debit=rec.debit,
                exit_reason=rec.exit_reason, pnl=rec.pnl
            ))

        # Detect orphaned open trades under a DIFFERENT strategy name -- e.g.
        # positional_risk.strategy was switched in config while a trade was
        # still open under the old name. Scoping _load()/maybe_enter() by
        # strategy_name (above) means this engine would otherwise never see
        # that trade and would happily open a second, uncoordinated position
        # while the old one sits unmanaged (never marked-to-market, never
        # closed) -- silent capital risk. This can't be fixed automatically
        # (the old strategy's engine isn't running), so surface it loudly.
        all_open = get_open_positional_trades()
        orphans = [r for r in all_open if r.strategy != self.strategy_name]
        if orphans:
            print(f"⚠️⚠️⚠️ [Positional] {len(orphans)} open trade(s) found under a "
                  f"DIFFERENT strategy than this engine ('{self.strategy_name}'): "
                  f"{[(r.strategy, r.trade_id, r.expiry) for r in orphans]}. "
                  f"These will NOT be managed (no mark-to-market, no exit) unless "
                  f"an engine for that strategy is also running. Check "
                  f"positional_risk.strategy / positional_risk.parallel in "
                  f"shared/config.yaml.")

    def maybe_enter(
        self, today: date, spot: float, vix: float, chain_premiums: dict[float, dict[str, float]],
        target_expiry: Optional[date] = None, trend_pct: float = 0.0,
    ) -> Optional[PositionalTrade]:
        """chain_premiums: {strike: {"CE": premium, "PE": premium}} for the target expiry.
        target_expiry: the REAL next weekly expiry as resolved from the live/historical
        option chain (holidays shift NSE expiries -- weekday arithmetic alone can miss
        those weeks). Falls back to weekday arithmetic only if not supplied."""
        if self.open_trade is not None:
            return None
        last_expiry = datetime.strptime(self.closed_trades[-1].expiry, "%Y-%m-%d").date() if self.closed_trades else None
        if not self.strategy.is_entry_day(today, last_expiry):
            return None

        expiry = target_expiry or self.strategy.next_expiry(today)
        dte = (expiry - today).days
        legs = self.strategy.size_legs(spot, vix, dte, trend_pct)

        try:
            # net credit received: shorts add premium in, longs cost premium out
            credit = sum((chain_premiums[l.strike][l.option_type] if l.side == "SHORT" else
                          -chain_premiums[l.strike][l.option_type]) for l in legs)
        except KeyError:
            return None
        if credit <= 0:
            return None
        if not self.strategy.should_enter(spot, credit):
            return None

        trade = PositionalTrade(
            id=str(uuid4()), strategy=self.strategy_name,
            entry_date=today.strftime("%Y-%m-%d"), expiry=expiry.strftime("%Y-%m-%d"),
            spot_at_entry=spot, vix_at_entry=vix, legs=legs, credit=round(credit, 2), quantity=LOT_SIZE,
        )
        self.open_trade = trade
        if not self.is_backtesting:
            from services.chartedge_core.database import persist_positional_entry
            persist_positional_entry(trade)
        return trade

    def mark_to_market(self, today: date, chain_premiums: dict[float, dict[str, float]]) -> Optional[PositionalTrade]:
        """Call once per trading day (or per tick, live) with current option premiums
        for the open trade's expiry. Closes the trade if profit-take/stop/expiry hit."""
        if self.open_trade is None:
            return None
        t = self.open_trade
        try:
            debit = t.mtm(chain_premiums)
        except KeyError:
            return None

        expiry_date = datetime.strptime(t.expiry, "%Y-%m-%d").date()
        reason = None
        if debit <= t.credit * (1 - self.strategy.profit_take_frac):
            reason = "PROFIT_TAKE"
        elif debit >= t.credit * self.strategy.stop_credit_mult:
            reason = "STOP_LOSS"
        elif today >= expiry_date:
            reason = "EXPIRY"

        if reason is None:
            return None

        t.exit_date = today.strftime("%Y-%m-%d")
        t.debit = round(debit, 2)
        t.exit_reason = reason
        t.pnl = round((t.credit - debit) * t.quantity, 2)
        t.status = "CLOSED"

        if not self.is_backtesting:
            from services.chartedge_core.database import persist_positional_exit
            persist_positional_exit(t.id, t.exit_date, t.debit, t.exit_reason, t.pnl)

        self.closed_trades.append(t)
        self.open_trade = None
        return t

    def metrics(self) -> dict:
        n = len(self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t.pnl > 0)
        total = sum(t.pnl for t in self.closed_trades)
        return {
            "strategy": self.strategy_name,
            "cycles": n,
            "wins": wins,
            "win_pct": round(wins / n * 100, 1) if n else 0.0,
            "net_pnl": round(total, 2),
            "capital": self.capital,
            "return_pct": round(total / self.capital * 100, 2) if self.capital else 0.0,
        }

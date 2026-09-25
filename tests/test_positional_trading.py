from datetime import date
from unittest.mock import patch

from services.chartedge_core.positional_trading import PositionalTradingEngine


def _make_closed_record(expiry: str, entry_date: str):
    """Build a lightweight stand-in for a PositionalTradeRecord row -- only the
    attributes _load() reads off it."""
    class _Row:
        pass
    r = _Row()
    r.trade_id = f"trade-{expiry}"
    r.strategy = "condor"
    r.entry_date = entry_date
    r.expiry = expiry
    r.spot_at_entry = 25000.0
    r.vix_at_entry = 13.0
    r.legs_json = '[{"strike": 24800.0, "option_type": "PE", "side": "SHORT"}, {"strike": 25200.0, "option_type": "CE", "side": "SHORT"}]'
    r.credit = 40.0
    r.quantity = 65
    r.status = "CLOSED"
    r.exit_date = entry_date
    r.debit = 5.0
    r.exit_reason = "PROFIT_TAKE"
    r.pnl = 2000.0
    return r


def _chain_from_engine_legs(engine, spot, vix, today, expiry):
    """Build a chain dict priced consistently with whatever strikes the
    strategy's own size_legs() actually picks (shorts priced above longs so
    net credit is positive), instead of guessing offsets by hand."""
    dte = (expiry - today).days
    legs = engine.strategy.size_legs(spot, vix, dte, 0.0)
    chain = {}
    for leg in legs:
        premiums = chain.setdefault(leg.strike, {"CE": 1.0, "PE": 1.0})
        premiums[leg.option_type] = 5.0 if leg.side == "SHORT" else 1.0
    return chain


def test_restart_reload_order_does_not_defeat_weekly_gate():
    """Regression for the bug found 2026-09-25: _load() populates closed_trades
    from a DB query ordered exit_date DESC (newest first), but the live
    in-process append() order is chronological (newest last). Using
    closed_trades[-1] to find "last_expiry" silently meant opposite things in
    the two code paths -- after a restart it pointed at the OLDEST trade in
    the reloaded window, so a still-current week's trade failed to block a
    fresh entry. This simulates exactly that: two closed records reloaded in
    DESC order (newest expiry first), and asserts build_entry() still refuses
    to enter for a date inside the most recent trade's expiry week."""
    # DESC order as the real DB query returns it: newest (09-08) first.
    closed_desc = [
        _make_closed_record(expiry="2026-09-08", entry_date="2026-09-02"),
        _make_closed_record(expiry="2026-08-25", entry_date="2026-08-19"),
    ]

    with patch("services.chartedge_core.database.create_db_and_tables"), \
         patch("services.chartedge_core.database.get_open_positional_trades", return_value=[]), \
         patch("services.chartedge_core.database.get_closed_positional_trades", return_value=closed_desc), \
         patch("services.chartedge_core.database.has_positional_trade_for_expiry", return_value=False):
        engine = PositionalTradingEngine(capital=100000.0, strategy_name="condor", is_backtesting=False)

        # Before the fix: closed_trades[-1] == the 2026-08-25 record (oldest of
        # the two, because the list was appended in DESC order), so
        # is_entry_day(today=2026-09-04, last_expiry=2026-08-25) returned True
        # -- a fresh entry would have been allowed even though the 09-08
        # expiry trade already covers this week.
        today = date(2026, 9, 4)
        expiry = date(2026, 9, 8)
        # Real, matching chain (not guessed strikes) -- otherwise a credit
        # KeyError could return None for the wrong reason and mask whether
        # the is_entry_day gate itself actually fired.
        chain = _chain_from_engine_legs(engine, spot=25000.0, vix=13.0, today=today, expiry=expiry)
        trade = engine.build_entry(today, spot=25000.0, vix=13.0, chain_premiums=chain,
                                    target_expiry=expiry)
        assert trade is None, (
            "build_entry allowed a new entry inside a week already covered by "
            "the most recently closed trade -- the reload-order bug is back."
        )


def test_db_guard_blocks_duplicate_expiry_even_if_memory_says_clear():
    """Defense-in-depth: has_positional_trade_for_expiry() must be consulted
    before every commit, independent of in-memory state. Simulate a totally
    clean in-memory engine (no open trade, no closed trade history at all --
    e.g. closed_trades wiped by whatever future bug) but a DB that already has
    a trade for the target expiry, and confirm build_entry still refuses."""
    with patch("services.chartedge_core.database.create_db_and_tables"), \
         patch("services.chartedge_core.database.get_open_positional_trades", return_value=[]), \
         patch("services.chartedge_core.database.get_closed_positional_trades", return_value=[]), \
         patch("services.chartedge_core.database.has_positional_trade_for_expiry", return_value=True):
        engine = PositionalTradingEngine(capital=100000.0, strategy_name="condor", is_backtesting=False)

        today = date(2026, 9, 2)
        # Empty on purpose: the DB guard must fire before chain_premiums is
        # ever touched, so a real chain isn't needed here.
        trade = engine.build_entry(today, spot=25000.0, vix=13.0, chain_premiums={},
                                    target_expiry=date(2026, 9, 8))
        assert trade is None, (
            "build_entry entered a trade despite the DB guard reporting a "
            "trade already exists for this expiry."
        )


def test_entry_still_allowed_for_a_genuinely_new_week():
    """Sanity check: the fixes above must not block legitimate entries. A
    clean engine with no prior trades and no DB conflict should still enter."""
    with patch("services.chartedge_core.database.create_db_and_tables"), \
         patch("services.chartedge_core.database.get_open_positional_trades", return_value=[]), \
         patch("services.chartedge_core.database.get_closed_positional_trades", return_value=[]), \
         patch("services.chartedge_core.database.has_positional_trade_for_expiry", return_value=False):
        engine = PositionalTradingEngine(capital=100000.0, strategy_name="condor", is_backtesting=False)

        today = date(2026, 9, 2)
        expiry = date(2026, 9, 8)
        chain = _chain_from_engine_legs(engine, spot=25000.0, vix=13.0, today=today, expiry=expiry)
        trade = engine.build_entry(today, spot=25000.0, vix=13.0, chain_premiums=chain,
                                    target_expiry=expiry)
        assert trade is not None
        assert trade.expiry == "2026-09-08"

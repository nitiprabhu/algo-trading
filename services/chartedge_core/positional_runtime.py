"""
positional_runtime.py
----------------------
Live wiring for positional_trading.py against the Upstox market-data
provider (see positional_data_provider.py -- UpstoxDataProvider).
Runs one check per trading day (idempotent -- safe to call
every few minutes). Sends Telegram alerts on entry/exit. Fully separate
from the intraday PaperTradingEngine/FuturesTradingEngine -- reads
market data read-only, never touches intraday positions or capital.

`market_runtime` is still accepted by check_once_per_day() but is now
used ONLY for _trend_pct() (intraday-candle-based; stays INDstocks-only
regardless of data_source -- out of scope for the Upstox swap). All
spot/VIX/option-chain/premium resolution goes through self.provider.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional
from zoneinfo import ZoneInfo

from services.chartedge_core.positional_trading import PositionalTradingEngine
from services.chartedge_core.positional_data_provider import MarketDataProvider

IST = ZoneInfo("Asia/Kolkata")


class PositionalRuntime:
    def __init__(self, engine: PositionalTradingEngine, config: dict, provider: MarketDataProvider):
        self.engine = engine
        self.config = config
        self.provider = provider
        self._last_check_date: Optional[date] = None

    async def check_once_per_day(self, market_runtime=None, force: bool = False) -> None:
        now = datetime.now(IST)
        today = now.date()
        # only run the check once fully resolved per day, and only during market hours
        # (force=True bypasses this, for manual/external triggers -- see
        # /api/positional/trigger, same semantics as positional_stocks_runtime's force=True)
        if not force and (now.time().hour < 9 or now.time().hour >= 16):
            return

        spot = await self.provider.get_spot("NIFTY")
        if spot is None:
            return
        vix = await self.provider.get_vix()

        if self.engine.open_trade is not None:
            legs = self.engine.open_trade.legs
            chain = await self.provider.get_option_chain(spot, "NIFTY", range_strikes=15, current_dt=now)
            premiums = await self.provider.get_leg_premiums(chain, legs)
            if premiums:
                open_legs = self.engine.open_trade.legs
                open_qty = self.engine.open_trade.quantity
                trade = self.engine.mark_to_market(today, premiums)
                if trade:
                    # exit stays commit-first: the paper close above is the
                    # decision of record; a failed live exit is alerted here
                    # and caught by reconcile_options_position.
                    _, live_note = self._execute_live(open_legs, open_qty, chain, entry=False)
                    await self._notify_exit(trade, live_note)
        else:
            chain = await self.provider.get_option_chain(
                spot, "NIFTY", range_strikes=15, current_dt=now,
                expiry_buffer_days=self.config.get("expiry_buffer_days", 1),
            )
            if not chain:
                return
            expiries_seen = sorted({row.get("expiry") for row in chain if row.get("expiry")})
            target_expiry = None
            if expiries_seen:
                try:
                    target_expiry = datetime.strptime(expiries_seen[0], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    target_expiry = self.engine.strategy.next_expiry(today)
            trend_pct = self._trend_pct(market_runtime) if market_runtime is not None else 0.0
            legs_needed = self._legs_for(spot, vix, today, target_expiry, trend_pct)
            premiums = await self.provider.get_leg_premiums(chain, legs_needed)
            if premiums:
                # Confirm-before-commit: build the candidate, place the live
                # basket FIRST, and only persist the paper trade if the broker
                # confirmed (or live is off/dry_run). A failed basket used to
                # leave a phantom paper position diverging from the real book
                # for a whole week -- same fix as the stocks module's
                # build_entry_candidate/_confirm_live_entry.
                candidate = self.engine.build_entry(today, spot, vix, premiums, target_expiry=target_expiry, trend_pct=trend_pct)
                if candidate:
                    committed, live_note = self._execute_live(candidate.legs, candidate.quantity, chain, entry=True)
                    if committed:
                        self.engine.commit_entry(candidate)
                        await self._notify_entry(candidate, live_note)
                    else:
                        from services.chartedge_core.telegram import notifier
                        await notifier.send_message(
                            f"⚠️ *[POSITIONAL] Weekly {candidate.strategy.title()} entry DROPPED*\n"
                            f"Broker basket failed -- no paper trade created.\n"
                            f"🏦 {live_note}"
                        )

        self._last_check_date = today

    def _execute_live(self, legs, quantity: int, chain: list[dict], entry: bool) -> tuple[bool, str]:
        """Mirror the paper decision as a real Upstox basket order, gated by
        positional_risk.live_trading (enabled+dry_run, common-pool margin
        check -- see upstox_options_broker.py). Returns (ok_to_commit, note):
        ok_to_commit is True whenever the paper record should proceed --
        live off entirely, dry_run/simulated, or a confirmed real basket --
        and False only when the broker is ARMED and the basket genuinely
        failed. Entry callers must drop the candidate on False (confirm-
        before-commit); exit callers ignore the flag (paper close already
        happened -- reconcile_options_position catches a failed live exit).
        Instrument keys come from the option-chain rows already fetched this
        cycle (Upstox pre-annotates ce/pe tokens)."""
        live_cfg = self.config.get("live_trading") or {}
        if not live_cfg.get("enabled", False):
            return True, ""
        try:
            from services.chartedge_core.upstox_options_broker import LegOrder, options_broker
            key_by_strike: dict[float, dict[str, str]] = {}
            for row in chain:
                key_by_strike[row.get("strike")] = {
                    "CE": row.get("ce_token", ""), "PE": row.get("pe_token", ""),
                }
            leg_orders = []
            for leg in legs:
                ikey = key_by_strike.get(leg.strike, {}).get(leg.option_type, "")
                if not ikey:
                    # missing key = can't mirror live at all. Not a broker
                    # rejection -- treat as commit-ok only when not armed.
                    broker = options_broker(live_cfg)
                    note = (f"⚠️ live skipped: no instrument key for "
                            f"{leg.option_type} {leg.strike:.0f} in chain")
                    return (not broker.is_armed()), note
                leg_orders.append(LegOrder(
                    instrument_key=ikey,
                    # entry: SHORT leg = SELL, LONG leg = BUY; exit reverses via close_basket
                    transaction_type="SELL" if leg.side == "SHORT" else "BUY",
                    quantity=quantity,
                    label=f"{leg.side} {leg.option_type} {leg.strike:.0f}",
                ))
            broker = options_broker(live_cfg)
            tag = "POS_CONDOR" if entry else "POS_CONDOR-EXIT"
            result = broker.place_basket(leg_orders, tag) if entry else broker.close_basket(leg_orders, tag)
            prefix = "🧪 DRY" if result.simulated else ("🟢 LIVE" if result.ok else "🔴 LIVE FAILED")
            print(f"[Positional/{'ENTRY' if entry else 'EXIT'}] {prefix}: {result.summary()}")
            return (result.ok or result.simulated), f"{prefix}: {result.summary()}"
        except Exception as e:
            print(f"⚠️ [Positional] live execution error: {e}")
            live_cfg_armed = live_cfg.get("enabled", False) and not live_cfg.get("dry_run", True)
            return (not live_cfg_armed), f"🔴 live execution error: {e}"

    def _legs_for(self, spot, vix, today, target_expiry, trend_pct):
        dte = max((target_expiry - today).days, 1) if target_expiry else 6
        return self.engine.strategy.size_legs(spot, vix, dte, trend_pct)

    def _trend_pct(self, market_runtime, lookback: int = 5) -> float:
        candles = market_runtime.candles.get("NIFTY", [])
        if len(candles) <= lookback:
            return 0.0
        old, new = candles[-lookback - 1].close, candles[-1].close
        return (new - old) / old * 100.0 if old else 0.0

    async def _notify_entry(self, trade, live_note: str = "") -> None:
        from services.chartedge_core.telegram import notifier
        legs_str = " | ".join(f"{leg.side[0]}{leg.option_type}{leg.strike:.0f}" for leg in trade.legs)
        msg = (
            f"🦅 *[POSITIONAL] Weekly {trade.strategy.title()} ENTERED*\n\n"
            f"📅 Entry: `{trade.entry_date}` → Expiry: `{trade.expiry}`\n"
            f"📊 Spot: `{trade.spot_at_entry:.2f}` | VIX: `{trade.vix_at_entry:.2f}`\n"
            f"🎯 Legs: {legs_str}\n"
            f"💰 Credit: `₹{trade.credit:.2f}` x {trade.quantity}"
        )
        if live_note:
            msg += f"\n🏦 Broker: {live_note}"
        await notifier.send_message(msg)

    async def _notify_exit(self, trade, live_note: str = "") -> None:
        from services.chartedge_core.telegram import notifier
        emoji = "✅" if trade.pnl >= 0 else "❌"
        msg = (
            f"{emoji} *[POSITIONAL] Weekly {trade.strategy.title()} CLOSED*\n\n"
            f"🔚 Reason: `{trade.exit_reason}`\n"
            f"💰 Credit: `₹{trade.credit:.2f}` → Debit: `₹{trade.debit:.2f}`\n"
            f"📈 PnL: `₹{trade.pnl:+.2f}`"
        )
        if live_note:
            msg += f"\n🏦 Broker: {live_note}"
        await notifier.send_message(msg)


def _leg_matches_position_row(leg, row: dict) -> bool:
    """Match a paper Leg to a live Upstox position row by trading symbol
    content (strike digits + CE/PE) -- the paper trade doesn't store
    instrument keys, and symbol formats vary across Upstox segments, so
    substring matching on the two unambiguous components is the robust
    option. Sign must also agree: SHORT leg -> net negative qty, LONG ->
    net positive."""
    symbol = (row.get("trading_symbol") or row.get("tradingsymbol") or "").upper()
    qty = int(row.get("quantity", 0) or 0)
    if leg.option_type not in symbol:
        return False
    if str(int(leg.strike)) not in symbol:
        return False
    if leg.side == "SHORT":
        return qty < 0
    return qty > 0


async def reconcile_options_position(engines: list[PositionalTradingEngine], live_cfg: dict) -> dict:
    """Paper-vs-broker reality check for the weekly options module -- the
    options counterpart of positional_stocks_runtime.reconcile_stock_positions.
    Catches both divergence directions:

      1. Paper OPEN, broker missing legs -- an entry basket that reported ok
         but didn't fully fill, or was manually squared off at the broker.
         The paper trade is being marked-to-market against a position that
         doesn't exist.
      2. Paper CLOSED (no open trade), broker still holds NIFTY option legs
         -- a failed/partial exit basket left REAL short-premium risk open
         with nothing managing it. The dangerous direction.

    Alert-only by design: multi-leg option state is never auto-corrected
    (auto-closing or auto-adopting legs could compound a partial-fill mess);
    every mismatch is surfaced loudly for manual action.

    No-op unless live_trading is armed (enabled and not dry_run) -- in paper/
    dry-run there is no real book to diverge from. Requires today's Upstox
    token; missing token is a no-op (never guess)."""
    from services.chartedge_core.upstox_broker import live_broker
    from services.chartedge_core.upstox_market_data import fetch_fo_positions
    from services.chartedge_core.telegram import notifier

    if not (live_cfg.get("enabled", False) and not live_cfg.get("dry_run", True)):
        return {"ran": False, "reason": "live_not_armed"}

    broker = live_broker()
    token = broker.get_valid_token()
    if not token:
        return {"ran": False, "reason": "no_valid_upstox_token"}

    rows = fetch_fo_positions(broker, token)
    # NIFTY index options only -- ignore equity intraday rows and other
    # underlyings; BANKNIFTY/FINNIFTY don't match the "NIFTY " prefix.
    nifty_rows = [
        r for r in rows
        if int(r.get("quantity", 0) or 0) != 0
        and (r.get("trading_symbol") or r.get("tradingsymbol") or "").upper().startswith("NIFTY")
    ]

    problems: list[str] = []
    open_trades = [(e.strategy_name, e.open_trade) for e in engines if e.open_trade is not None]

    matched_row_ids: set[int] = set()
    for strategy_name, trade in open_trades:
        from services.chartedge_core.positional_trading import Leg
        legs = [leg if isinstance(leg, Leg) else Leg(**leg) for leg in trade.legs]
        missing = []
        for leg in legs:
            hit = next((r for r in nifty_rows
                        if id(r) not in matched_row_ids and _leg_matches_position_row(leg, r)), None)
            if hit is None:
                missing.append(f"{leg.side} {leg.option_type} {leg.strike:.0f}")
            else:
                matched_row_ids.add(id(hit))
        if missing:
            problems.append(
                f"paper '{strategy_name}' trade {trade.id[:8]} (expiry {trade.expiry}) is OPEN "
                f"but broker is missing leg(s): {', '.join(missing)} -- "
                f"paper is marking-to-market a position that isn't fully there"
            )

    unmatched = [r for r in nifty_rows if id(r) not in matched_row_ids]
    if unmatched:
        desc = ", ".join(
            f"{(r.get('trading_symbol') or r.get('tradingsymbol'))} qty={r.get('quantity')}"
            for r in unmatched
        )
        if open_trades:
            problems.append(f"broker holds NIFTY option leg(s) not matching any paper trade: {desc}")
        else:
            problems.append(
                f"NO paper trade open but broker still holds NIFTY option leg(s): {desc} -- "
                f"likely a failed/partial exit basket; REAL risk is unmanaged, close manually"
            )

    if problems:
        await notifier.send_message(
            "🚨 *[RECONCILE] Weekly options paper vs Upstox positions*\n\n"
            + "\n".join(f"• {p}" for p in problems)
            + "\n\nNothing auto-corrected -- multi-leg state needs manual review."
        )

    return {"ran": True, "problems": problems,
            "live_nifty_option_rows": len(nifty_rows),
            "open_paper_trades": len(open_trades)}

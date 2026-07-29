"""
positional_stocks_runtime.py
-----------------------------
Live wiring for positional_stocks.py. Fully decoupled from the INDmoney/
indstocks intraday feed -- fetches daily OHLCV directly from yfinance
(NSE tickers, e.g. RELIANCE.NS), which needs no API token/auth and never
expires, so this module runs unattended with zero daily intervention.
Runs genuinely once per calendar day, after a post-close cutoff (default
15:35 IST) -- daily-swing technical investment, not intraday trading.
Computes confluence and calls the engine's BUY-only maybe_enter() /
check_exit(). Sends Telegram alerts tagged "[POSITIONAL STOCKS]" via the
same global notifier used elsewhere, distinct from the options positional
module's "[POSITIONAL]" tag.
"""
from __future__ import annotations

from datetime import datetime, date, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from services.chartedge_core.models import Candle
from services.chartedge_core.positional_stocks import (
    PositionalStocksEngine, compute_stock_signal,
)

IST = ZoneInfo("Asia/Kolkata")

# yfinance NSE tickers append .NS; extend as symbols are added.
YFINANCE_TICKER_SUFFIX = ".NS"


def _yf_ticker(symbol: str) -> str:
    return f"{symbol}{YFINANCE_TICKER_SUFFIX}"


def fetch_daily_candles(symbol: str, period: str = "1y") -> list[Candle]:
    """Pull daily OHLCV from yfinance -- no token, no auth, no expiry.
    period="1y" is enough for most indicators here; Golden Cross/Cup&Handle
    need ~200+ bars so a longer period helps once accumulated."""
    ticker = _yf_ticker(symbol)
    df = yf.download(ticker, period=period, interval="1d", progress=False)
    if df.empty:
        return []
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    # yfinance can return NaN rows (today's still-forming bar, holidays, splits).
    # A NaN close flows into indicators and blows up statistics.pstdev
    # ("'float' object has no attribute 'numerator'"), 500-ing the whole run.
    # Drop any row missing an OHLCV value so only clean bars reach the engine.
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    if df.empty:
        return []
    candles = []
    for ts, row in df.iterrows():
        candles.append(Candle(
            time=ts.to_pydatetime(), instrument=symbol, timeframe="1D",
            open=float(row["Open"]), high=float(row["High"]), low=float(row["Low"]),
            close=float(row["Close"]), volume=int(row["Volume"]),
        ))
    return candles


class PositionalStocksRuntime:
    def __init__(self, engine: PositionalStocksEngine, config: dict):
        self.engine = engine
        self.config = config
        self._last_check_date: date | None = None

    async def check_once_per_day(self, force: bool = False) -> dict:
        """Run one analysis pass over all configured symbols.

        force=True bypasses the once-per-day guard and the post-close cutoff
        time, for manual/external triggers (e.g. a GitHub Actions workflow).
        Returns a summary dict of what happened this run.
        """
        now = datetime.now(IST)
        today = now.date()

        if not force:
            if self._last_check_date == today:
                return {"ran": False, "reason": "already_checked_today"}
            cutoff = self.config.get("check_after_time", "15:35")
            cutoff_h, cutoff_m = (int(x) for x in cutoff.split(":"))
            if now.time() < dtime(cutoff_h, cutoff_m):
                return {"ran": False, "reason": "before_cutoff_time"}
            # Upper bound: NSE closes 15:30 IST and place_entry() always sends
            # is_amo=False, so any live BUY placed after close gets instantly
            # rejected by the exchange (not queued) -- this bit us for real on
            # 2026-07-22 when a service restart after close wiped the in-memory
            # once-per-day guard and immediately re-fired past the (pre-close)
            # cutoff. _last_check_date is only in-memory, so a restart any time
            # after cutoff can retrigger this; capping the window here is
            # cheaper and safer than making place_entry AMO-aware.
            close_cutoff = self.config.get("market_close_time", "15:25")
            close_h, close_m = (int(x) for x in close_cutoff.split(":"))
            if now.time() >= dtime(close_h, close_m):
                self._last_check_date = today  # don't retry today -- window's gone
                return {"ran": False, "reason": "past_market_close_window"}

        entries: list[str] = []
        exits: list[str] = []
        rotations: list[str] = []
        symbols: list[str] = self.config.get("symbols", [])
        buy_threshold = self.config.get("buy_threshold", 0.35)
        sell_threshold = self.config.get("sell_threshold", -0.35)
        min_adx = self.config.get("min_adx", 25.0)
        require_trend_gate = self.config.get("require_trend_gate", True)
        trail_arm_pct = self.config.get("trail_arm_pct", 3.0)
        trail_keep_frac = self.config.get("trail_keep_frac", 0.5)
        allow_rotation = self.config.get("allow_rotation", False)
        rotation_margin = self.config.get("rotation_margin", 0.15)
        weights = self.config.get("indicator_weights", {})
        yf_period = self.config.get("yfinance_period", "1y")

        # scores/prices for every configured symbol computed this run -- reused by the
        # rotation pass below so a blocked-by-capacity BUY can compare against the
        # weakest currently open position without re-fetching/re-scoring anything.
        scores_today: dict[str, float] = {}
        prices_today: dict[str, float] = {}
        pending_rotation_candidates: list[tuple[str, float]] = []

        for symbol in symbols:
            try:
                daily = fetch_daily_candles(symbol, period=yf_period)
            except Exception as e:
                print(f"[Positional Stocks] yfinance fetch failed for {symbol}: {e}")
                continue
            if len(daily) < 20:
                continue  # not enough daily history yet for any indicator to be meaningful

            score, indicators = compute_stock_signal(daily, weights.get(symbol, {}))
            price = daily[-1].close
            adx_value = indicators["adx"].value
            scores_today[symbol] = score
            prices_today[symbol] = price

            if symbol in self.engine.open_positions:
                closed = self.engine.check_exit(
                    symbol, today, price, score, sell_threshold,
                    trail_arm_pct=trail_arm_pct, trail_keep_frac=trail_keep_frac,
                )
                if closed:
                    await self._notify_exit(closed)
                    await self._live_exit(closed)
                    exits.append(symbol)
            else:
                trend_confirmed = True
                if require_trend_gate:
                    trend_confirmed = indicators["ema_ribbon"].vote == 1 and indicators["supertrend"].vote == 1
                qualifies = score >= buy_threshold and adx_value >= min_adx and trend_confirmed
                candidate = self.engine.build_entry_candidate(
                    symbol, today, price, score, buy_threshold, adx_value, min_adx,
                    trend_confirmed=trend_confirmed,
                )
                if candidate:
                    if await self._confirm_live_entry(candidate, price):
                        self.engine.commit_entry(candidate)
                        await self._notify_entry(candidate, score)
                        entries.append(symbol)
                    # else: broker rejected/unfilled -- candidate dropped, nothing persisted
                elif qualifies and allow_rotation:
                    # fully qualifies but blocked purely by pool capacity/capital --
                    # defer to the rotation pass below, once every symbol's score is known.
                    pending_rotation_candidates.append((symbol, score))

        if allow_rotation and pending_rotation_candidates:
            # strongest candidate first -- if it doesn't clear the rotation margin
            # against the weakest holding, weaker candidates won't either.
            pending_rotation_candidates.sort(key=lambda x: x[1], reverse=True)
            for symbol, score in pending_rotation_candidates:
                result = self.engine.maybe_rotate_and_enter(
                    symbol, today, prices_today[symbol], score, buy_threshold,
                    open_scores=scores_today, open_prices=prices_today,
                    trail_arm_pct=trail_arm_pct, rotation_margin=rotation_margin,
                )
                if result:
                    closed, opened = result
                    await self._notify_rotation(closed, opened, score)
                    # order matters: close the old live position (frees the
                    # holding + cancels its GTT context) before opening the new one.
                    await self._live_exit(closed)
                    await self._live_entry(opened, prices_today[symbol])
                    exits.append(closed.symbol)
                    entries.append(symbol)
                    rotations.append(f"{closed.symbol}->{symbol}")

        self._last_check_date = today
        return {
            "ran": True,
            "checked_symbols": symbols,
            "entries": entries,
            "exits": exits,
            "rotations": rotations,
            "forced": force,
        }

    def _live_tag(self) -> str:
        """Upstox order tag grouping this pool's live orders (<=40 chars)."""
        return f"POS_{self.engine.pool.upper()}"[:40]

    async def _maybe_request_token(self, broker) -> None:
        """Event-driven WhatsApp/app approval: only ask when there's actually
        something to execute live and no valid token exists yet. Delegates to
        the shared upstox_broker.notify_token_needed(), which also backs the
        weekly-positional Upstox data provider -- one implementation, one
        Telegram message format, deduped process-wide either way."""
        from services.chartedge_core.upstox_broker import notify_token_needed
        await notify_token_needed(reason=f"{self.engine.pool} pool")

    async def _live_entry(self, position, ref_price: float) -> None:
        """Fire a live Upstox BUY + protective GTT stop for an already-committed
        entry (rotation path only -- see maybe_rotate_and_enter). No-op
        unless live_trading is armed; on dry_run it just logs. Never raises --
        the paper record is already committed here, so a broker failure must
        not roll that back; it only alerts. The regular (non-rotation) entry
        path instead confirms the broker BEFORE committing -- see
        _confirm_live_entry -- specifically to avoid this phantom-record
        failure mode; rotation still uses this older commit-first flow."""
        from services.chartedge_core.upstox_broker import live_broker, log_order_event
        from services.chartedge_core.telegram import notifier
        broker = live_broker()
        if not broker.enabled:
            return  # live path entirely off -> pure paper, stay silent
        if not broker.dry_run:
            await self._maybe_request_token(broker)
        res = broker.place_entry(position.symbol, position.quantity, ref_price, self._live_tag())
        log_order_event("BUY", position.symbol, res)
        mode = "SIM" if res.simulated else "LIVE"
        if res.ok:
            await notifier.send_message(
                f"[{mode} ORDER] BUY {position.symbol} x{position.quantity} "
                f"tag={self._live_tag()} | {res.reason}"
            )
        else:
            await notifier.send_message(
                f"⚠️ [{mode} ORDER FAILED] BUY {position.symbol} -- {res.reason}. "
                f"Paper position stands; no live fill."
            )

    async def _confirm_live_entry(self, candidate, ref_price: float) -> bool:
        """Fire the live Upstox BUY for an as-yet-uncommitted candidate
        (build_entry_candidate) and report whether it's safe to commit to the
        DB: True when live trading is off entirely (pure paper -- always
        commit) or the broker actually confirms the fill; False when the
        broker is armed and rejected/failed the order, so the caller must
        discard the candidate instead of persisting a phantom position."""
        from services.chartedge_core.upstox_broker import live_broker, log_order_event
        from services.chartedge_core.telegram import notifier
        broker = live_broker()
        if not broker.enabled:
            return True  # live path entirely off -> pure paper, always commit
        if not broker.dry_run:
            await self._maybe_request_token(broker)
        res = broker.place_entry(candidate.symbol, candidate.quantity, ref_price, self._live_tag())
        log_order_event("BUY", candidate.symbol, res)
        mode = "SIM" if res.simulated else "LIVE"
        if res.ok:
            await notifier.send_message(
                f"[{mode} ORDER] BUY {candidate.symbol} x{candidate.quantity} "
                f"tag={self._live_tag()} | {res.reason}"
            )
            return True
        await notifier.send_message(
            f"⚠️ [{mode} ORDER FAILED] BUY {candidate.symbol} -- {res.reason}. "
            f"No DB entry created (broker did not confirm)."
        )
        return False

    async def _live_exit(self, position) -> None:
        """Fire a live Upstox SELL to close a live position on an engine exit.
        If no valid token, the sell is skipped and the position's server-side
        GTT stop remains the protective floor -- surfaced via alert. On a
        real successful sell, also cancel that symbol's protective GTT stop
        -- otherwise it's left dangling at the broker with no holding behind
        it (harmless -- it'd just fail to trigger -- but clutters the GTT
        book; the same class of orphan the reconcile job cleans up for the
        never-filled case, here handled immediately at exit time instead)."""
        from services.chartedge_core.upstox_broker import live_broker, log_order_event
        from services.chartedge_core.telegram import notifier
        broker = live_broker()
        if not broker.enabled:
            return
        if not broker.dry_run:
            await self._maybe_request_token(broker)
        res = broker.place_exit(position.symbol, position.quantity, self._live_tag())
        log_order_event("SELL", position.symbol, res)
        mode = "SIM" if res.simulated else "LIVE"
        if res.ok:
            await notifier.send_message(
                f"[{mode} ORDER] SELL {position.symbol} x{position.quantity} | {res.reason}"
            )
            if not res.simulated:
                await self._cancel_gtt_for_symbol(broker, position.symbol)
        else:
            await notifier.send_message(
                f"⚠️ [{mode} ORDER FAILED] SELL {position.symbol} -- {res.reason}"
            )

    async def _cancel_gtt_for_symbol(self, broker, symbol: str) -> None:
        """Cancel any SCHEDULED GTT stop for symbol -- called right after a
        confirmed real SELL so the protective stop doesn't sit dangling at
        the broker with no holding behind it."""
        from services.chartedge_core.telegram import notifier
        token = broker.get_valid_token()
        if not token:
            return
        for g in broker.list_gtt(token):
            if g.get("trading_symbol") != symbol:
                continue
            if g.get("rules", [{}])[0].get("status") != "SCHEDULED":
                continue
            gtt_id = g.get("gtt_order_id")
            res = broker.cancel_gtt(gtt_id, token)
            if not res.ok:
                await notifier.send_message(
                    f"⚠️ [Positional Stocks] GTT cancel failed for {symbol} ({gtt_id}) "
                    f"after sell: {res.reason}"
                )

    async def _notify_entry(self, position, score: float) -> None:
        from services.chartedge_core.telegram import notifier
        tag = "POSITIONAL STOCKS" if self.engine.pool == "largecap" else f"POSITIONAL STOCKS {self.engine.pool.upper()}"
        msg = (
            f"[{tag}] BUY {position.symbol}\n\n"
            f"Entry: {position.entry_date} @ {position.entry_price}\n"
            f"Quantity: {position.quantity}\n"
            f"Confluence score: {score:.3f}"
        )
        await notifier.send_message(msg)

    async def _notify_rotation(self, closed, opened, new_score: float) -> None:
        from services.chartedge_core.telegram import notifier
        tag = "POSITIONAL STOCKS" if self.engine.pool == "largecap" else f"POSITIONAL STOCKS {self.engine.pool.upper()}"
        msg = (
            f"[{tag}] ROTATED {closed.symbol} -> {opened.symbol}\n\n"
            f"Sold {closed.symbol}: {closed.entry_price} -> {closed.exit_price} "
            f"(PnL {closed.pnl:+.2f} / {closed.pnl_pct:+.2f}%)\n"
            f"Bought {opened.symbol}: {opened.entry_date} @ {opened.entry_price} "
            f"x{opened.quantity} (score {new_score:.3f})\n"
            f"Reason: pool fully deployed, new signal outscored weakest holding"
        )
        await notifier.send_message(msg)

    async def _notify_exit(self, position) -> None:
        from services.chartedge_core.telegram import notifier
        tag = "POSITIONAL STOCKS" if self.engine.pool == "largecap" else f"POSITIONAL STOCKS {self.engine.pool.upper()}"
        emoji = "profit" if position.pnl >= 0 else "loss"
        msg = (
            f"[{tag}] SELL {position.symbol} ({emoji})\n\n"
            f"Reason: {position.exit_reason}\n"
            f"Entry: {position.entry_price} -> Exit: {position.exit_price}\n"
            f"PnL: {position.pnl:+.2f} ({position.pnl_pct:+.2f}%)"
        )
        await notifier.send_message(msg)


async def reconcile_stock_positions(engines: dict[str, "PositionalStocksEngine"]) -> dict:
    """Post-close reality check: pull actual Upstox CNC holdings and correct
    any DB position that claims OPEN but was never actually filled at the
    broker. Needed because _live_entry() commits the paper record before
    the live order result comes back (by design -- a broker failure must
    not silently roll back the paper trade) and only alerts on failure, so
    a missed/rejected fill (funds, RMS block, price band, etc.) otherwise
    leaves the DB permanently out of sync with the real book. Symbols held
    at Upstox but untracked in any pool (e.g. a manual buy) are reported,
    never auto-adopted into a pool's DB.

    engines: {pool_name: PositionalStocksEngine}. Requires a valid today's
    Upstox token; a stale/missing token is a no-op (never guess)."""
    from services.chartedge_core.database import persist_stock_exit
    from services.chartedge_core.upstox_broker import live_broker
    from services.chartedge_core.upstox_market_data import fetch_holdings, fetch_order_book
    from services.chartedge_core.telegram import notifier

    broker = live_broker()
    token = broker.get_valid_token()
    if not token:
        return {"ran": False, "reason": "no_valid_upstox_token"}

    holdings = fetch_holdings(broker, token)
    held_qty: dict[str, int] = {}
    for h in holdings:
        symbol = h.get("tradingsymbol")
        if symbol:
            held_qty[symbol] = held_qty.get(symbol, 0) + int(h.get("quantity", 0) or 0)

    # Real SELL fill prices from today's order book -- weighted-average
    # across completed SELL orders per symbol, for positions that turn out
    # to have actually sold (see branch below) rather than never filled.
    orders = fetch_order_book(broker, token)
    sell_fill: dict[str, tuple[float, int]] = {}  # symbol -> (total_value, total_qty)
    for o in orders:
        symbol = o.get("trading_symbol") or o.get("tradingsymbol")
        status = (o.get("status") or "").lower()
        if not symbol or o.get("transaction_type") != "SELL" or status != "complete":
            continue
        qty = int(o.get("filled_quantity", 0) or 0)
        avg_price = float(o.get("average_price", 0) or 0)
        if qty <= 0 or avg_price <= 0:
            continue
        total_value, total_qty = sell_fill.get(symbol, (0.0, 0))
        sell_fill[symbol] = (total_value + avg_price * qty, total_qty + qty)

    # Today's BUY order status per symbol. Needed because
    # /portfolio/long-term-holdings reflects T+1 SETTLED holdings -- a stock
    # bought today never appears there until tomorrow regardless of whether
    # the buy actually succeeded. Checking held_qty alone for a same-day
    # entry would wrongly close every genuinely-filled same-day BUY too, not
    # just failed ones (this bit us for real: ADANIENSOL/FEDERALBNK/others
    # got zeroed out this way before this fix). "complete" -> really filled,
    # keep OPEN even though holdings won't show it until tomorrow.
    # "rejected"/"cancelled" -> genuinely never filled, safe to close.
    # Anything else (open/trigger pending/etc) -> still in flight, leave
    # alone and re-check next run.
    buy_status: dict[str, str] = {}
    for o in orders:
        symbol = o.get("trading_symbol") or o.get("tradingsymbol")
        if not symbol or o.get("transaction_type") != "BUY":
            continue
        status = (o.get("status") or "").lower()
        # complete takes priority over any other status seen for the symbol
        if status == "complete" or buy_status.get(symbol) != "complete":
            buy_status[symbol] = status

    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    removed: list[str] = []
    reconciled_exits: list[str] = []
    ambiguous: list[str] = []
    tracked_symbols: set[str] = set()

    for pool, engine in engines.items():
        tracked_symbols.update(engine.open_positions.keys())
        tracked_symbols.update(p.symbol for p in engine.closed_positions)
        for symbol, pos in list(engine.open_positions.items()):
            if held_qty.get(symbol, 0) >= pos.quantity:
                continue  # broker confirms this quantity is genuinely held

            if symbol in sell_fill:
                # Today's order book positively confirms a completed SELL --
                # a real exit happened outside our own exit path (GTT stop
                # trigger, manual sell, or a live_exit() that fired but
                # silently failed to notify) and the DB never learned about
                # it. This is the only case we trust enough to auto-close
                # with a computed price, because we have direct evidence.
                total_value, total_qty = sell_fill[symbol]
                exit_price = round(total_value / total_qty, 2)
                pnl = round((exit_price - pos.entry_price) * pos.quantity, 2)
                pnl_pct = round((exit_price - pos.entry_price) / pos.entry_price * 100, 2) if pos.entry_price else 0.0
                pos.status = "CLOSED"
                pos.exit_date = today_str
                pos.exit_price = exit_price
                pos.exit_reason = "RECONCILED_LIVE_EXIT"
                pos.pnl = pnl
                pos.pnl_pct = pnl_pct
                persist_stock_exit(pos.id, today_str, exit_price, "RECONCILED_LIVE_EXIT", pnl, pnl_pct, pos.peak_pnl_pct)
                engine.closed_positions.append(pos)
                del engine.open_positions[symbol]
                reconciled_exits.append(f"{pool}:{symbol} @ {exit_price}")
            elif pos.entry_date == today_str and buy_status.get(symbol) == "complete":
                # BUY genuinely filled today (order book confirms it) --
                # its absence from holdings is just T+1 settlement lag, not
                # a real problem. Leave OPEN; holdings will show it tomorrow.
                continue
            elif pos.entry_date == today_str and buy_status.get(symbol) in ("rejected", "cancelled"):
                # BUY genuinely never filled today -- safe to close.
                pos.status = "CLOSED"
                pos.exit_date = today_str
                pos.exit_price = pos.entry_price
                pos.exit_reason = "RECONCILED_NOT_FILLED"
                pos.pnl = 0.0
                pos.pnl_pct = 0.0
                persist_stock_exit(pos.id, today_str, pos.entry_price, "RECONCILED_NOT_FILLED", 0.0, 0.0, pos.peak_pnl_pct)
                engine.closed_positions.append(pos)
                del engine.open_positions[symbol]
                removed.append(f"{pool}:{symbol}")
            elif pos.entry_date == today_str:
                # Order still in flight (open/pending) or no matching order
                # found in today's book at all -- don't guess, re-check next run.
                continue
            else:
                # Older position, no holding, no confirming SELL in today's
                # order book -- ambiguous. Could be a real exit from a day
                # reconcile didn't run, or a BUY that never filled in the
                # first place and has sat OPEN undetected since. Upstox's
                # order book is today-only so neither can be verified here.
                # Never guess a price either way -- flag for manual review
                # and leave the DB record untouched.
                ambiguous.append(f"{pool}:{symbol} (DB qty {pos.quantity}, entered {pos.entry_date})")

    untracked = sorted(
        symbol for symbol, qty in held_qty.items()
        if qty > 0 and symbol not in tracked_symbols
    )

    # Orphaned GTT stops: place_entry() fires the protective GTT right after
    # the BUY appears to succeed, but a since-corrected (RECONCILED_NOT_FILLED)
    # or never-fully-filled position leaves that SELL-side GTT alive at the
    # broker with no real holding behind it -- clean those up so a dangling
    # SCHEDULED order isn't sitting there indefinitely (harmless -- it'd just
    # fail to execute -- but it's clutter and confusing to audit manually).
    cancelled_gtt: list[str] = []
    for g in broker.list_gtt(token):
        if g.get("rules", [{}])[0].get("status") != "SCHEDULED":
            continue
        symbol = g.get("trading_symbol")
        gtt_qty = int(g.get("quantity", 0) or 0)
        if held_qty.get(symbol, 0) >= gtt_qty:
            continue  # broker holding covers this GTT's sell quantity -- legit
        gtt_id = g.get("gtt_order_id")
        res = broker.cancel_gtt(gtt_id, token)
        if res.ok:
            cancelled_gtt.append(f"{symbol}({gtt_id})")
        else:
            print(f"⚠️ [Reconcile] GTT cancel failed for {symbol} {gtt_id}: {res.reason}")

    if removed or reconciled_exits or ambiguous or untracked or cancelled_gtt:
        lines = ["[RECONCILE] Positional stocks vs Upstox holdings"]
        if removed:
            lines.append("Removed (never filled, DB corrected): " + ", ".join(removed))
        if reconciled_exits:
            lines.append("Sold at broker but DB still showed OPEN -- closed with recovered PnL: "
                          + ", ".join(reconciled_exits))
        if ambiguous:
            lines.append("⚠️ DB shows OPEN but broker doesn't hold it, and no confirming SELL in "
                         "today's order book -- can't tell if never filled or sold on an earlier "
                         "day. NOT auto-corrected, needs manual check: " + ", ".join(ambiguous))
        if untracked:
            lines.append("Held at broker but untracked by any pool (manual?): " + ", ".join(untracked))
        if cancelled_gtt:
            lines.append("Orphaned GTT stops cancelled: " + ", ".join(cancelled_gtt))
        await notifier.send_message("\n".join(lines))

    return {"ran": True, "removed": removed, "reconciled_exits": reconciled_exits,
            "ambiguous": ambiguous, "untracked": untracked, "cancelled_gtt": cancelled_gtt}

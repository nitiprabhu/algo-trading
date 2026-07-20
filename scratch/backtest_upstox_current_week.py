"""
backtest_upstox_current_week.py
---------------------------------
Replays the weekly condor strategy over the current, still-open NIFTY
expiry cycle using 100% real Upstox data (historical NIFTY/VIX daily
candles + historical option-leg candles). A true "last week" backtest
isn't reachable via Upstox: /v2/option/chain and /v2/option/contract
both 401 for already-expired expiries, so there's no way to rediscover
which instrument keys existed for a settled contract. This script
instead replays Mon -> today of the CURRENT cycle (2026-07-21 expiry),
which is still live and therefore fully queryable.

Deliberately does NOT use PositionalTradingEngine (it persists every
entry/exit to the real chartedge.db / Postgres via
persist_positional_entry/persist_positional_exit with no backtest
flag) -- this script reimplements the exact same math from
positional_trading.py's WeeklyCondorStrategy.size_legs() / maybe_enter()
/ mark_to_market() as pure functions so nothing touches production data.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import requests
from services.chartedge_core.upstox_broker import live_broker
from services.chartedge_core.positional_trading import (
    WeeklyCondorStrategy, Leg, PROFIT_TAKE_FRAC, STOP_CREDIT_MULT, LOT_SIZE,
)

NIFTY_KEY = "NSE_INDEX|Nifty 50"
VIX_KEY = "NSE_INDEX|India VIX"
CAPITAL = 100_000.0


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def fetch_daily_history(broker, token: str, instrument_key: str, from_date: date, to_date: date) -> dict[date, float]:
    url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/day/{to_date}/{from_date}"
    resp = requests.get(url, headers=_headers(token), timeout=15)
    resp.raise_for_status()
    candles = resp.json().get("data", {}).get("candles", [])
    out = {}
    for c in candles:
        d = datetime.fromisoformat(c[0]).date()
        out[d] = float(c[4])  # close
    return out


def fetch_live_chain(broker, token: str, expiry: date) -> list[dict]:
    resp = requests.get(
        "https://api.upstox.com/v2/option/chain", headers=_headers(token),
        params={"instrument_key": NIFTY_KEY, "expiry_date": expiry.strftime("%Y-%m-%d")}, timeout=15,
    )
    resp.raise_for_status()
    rows = resp.json().get("data", []) or []
    chain = []
    for row in rows:
        strike = row.get("strike_price")
        if strike is None:
            continue
        ce = row.get("call_options") or {}
        pe = row.get("put_options") or {}
        chain.append({
            "strike": float(strike),
            "ce_key": ce.get("instrument_key", ""),
            "pe_key": pe.get("instrument_key", ""),
        })
    return chain


def main():
    broker = live_broker()
    token = broker.get_valid_token()
    if not token:
        print("No valid Upstox token -- run the WhatsApp approval flow first.")
        return

    today = date.today()
    lookback_start = today - timedelta(days=17)  # enough to span the prior cycle's expiry too

    strategy = WeeklyCondorStrategy()
    expiry = strategy.next_expiry(today)  # the CURRENT still-open cycle's expiry (e.g. 2026-07-21)

    nifty_hist = fetch_daily_history(broker, token, NIFTY_KEY, lookback_start, today)
    vix_hist = fetch_daily_history(broker, token, VIX_KEY, lookback_start, today)

    # Find the true start of the current cycle: the earliest day whose
    # next_expiry() also resolves to `expiry` (i.e. the first trading day
    # after the PRIOR cycle's expiry passed). A naive "Monday of this week"
    # can land inside the prior, already-expired cycle instead.
    candidate_days = sorted(d for d in nifty_hist if d <= today)
    cycle_days = [d for d in candidate_days if strategy.next_expiry(d) == expiry]
    if not cycle_days:
        print(f"No trading days found belonging to the {expiry} cycle in range.")
        return
    entry_day = cycle_days[0]
    trading_days = [d for d in cycle_days if d <= today]

    spot_entry = nifty_hist[entry_day]
    vix_entry = vix_hist.get(entry_day, 15.0)
    dte = (expiry - entry_day).days
    legs = strategy.size_legs(spot_entry, vix_entry, dte)

    print(f"=== Replaying condor cycle: {entry_day} -> {today} (expiry {expiry}, Upstox real data) ===\n")

    print(f"Entry day: {entry_day} | spot={spot_entry:.2f} | VIX={vix_entry:.2f} | expiry={expiry} (dte={dte})")
    print(f"Legs: {[(l.strike, l.option_type, l.side) for l in legs]}\n")

    chain = fetch_live_chain(broker, token, expiry)
    strike_set = {l.strike for l in legs}
    leg_keys: dict[float, dict[str, str]] = {}
    for row in chain:
        if row["strike"] in strike_set:
            leg_keys[row["strike"]] = {"CE": row["ce_key"], "PE": row["pe_key"]}
    missing = strike_set - leg_keys.keys()
    if missing:
        print(f"WARNING: no instrument keys found for strikes {missing} (outside chain range?)")

    # Pull each leg's historical daily candle across the replay window.
    leg_history: dict[float, dict[str, dict[date, float]]] = {}
    for strike, keys in leg_keys.items():
        leg_history[strike] = {}
        for opt_type in ("CE", "PE"):
            key = keys.get(opt_type)
            if not key:
                continue
            try:
                leg_history[strike][opt_type] = fetch_daily_history(broker, token, key, entry_day, today)
            except Exception as e:
                print(f"  leg fetch failed for {strike}{opt_type} ({key}): {e}")
                leg_history[strike][opt_type] = {}

    def premiums_for(d: date) -> dict[float, dict[str, float]]:
        out = {}
        for strike in strike_set:
            entry = {}
            for opt_type in ("CE", "PE"):
                px = leg_history.get(strike, {}).get(opt_type, {}).get(d)
                if px is not None:
                    entry[opt_type] = px
            if entry:
                out[strike] = entry
        return out

    entry_premiums = premiums_for(entry_day)
    try:
        credit = sum((entry_premiums[l.strike][l.option_type] if l.side == "SHORT" else
                      -entry_premiums[l.strike][l.option_type]) for l in legs)
    except KeyError as e:
        print(f"Missing entry-day premium for {e} -- cannot compute credit. Chain rows near these strikes:")
        for row in chain:
            if abs(row["strike"] - spot_entry) < 500:
                print(f"  {row}")
        return

    print(f"Entry credit: {credit:.2f} x {LOT_SIZE} = Rs {credit * LOT_SIZE:,.2f}\n")

    status = "OPEN"
    exit_day = None
    exit_reason = None
    debit = None
    for d in trading_days[1:]:
        prem = premiums_for(d)
        try:
            day_debit = sum((prem[l.strike][l.option_type] if l.side == "SHORT" else
                             -prem[l.strike][l.option_type]) for l in legs)
        except KeyError:
            print(f"{d}: missing premium data, skipping")
            continue
        pnl_today = (credit - day_debit) * LOT_SIZE
        print(f"{d}: debit={day_debit:.2f} | unrealized PnL=Rs {pnl_today:,.2f}")

        if day_debit <= credit * (1 - PROFIT_TAKE_FRAC):
            status, exit_day, exit_reason, debit = "CLOSED", d, "PROFIT_TAKE", day_debit
            break
        if day_debit >= credit * STOP_CREDIT_MULT:
            status, exit_day, exit_reason, debit = "CLOSED", d, "STOP_LOSS", day_debit
            break
        if d >= expiry:
            status, exit_day, exit_reason, debit = "CLOSED", d, "EXPIRY", day_debit
            break

    print(f"\n=== Result ===")
    print(f"Status: {status}")
    if status == "CLOSED":
        pnl = (credit - debit) * LOT_SIZE
        print(f"Exit day: {exit_day} | reason: {exit_reason} | debit: {debit:.2f}")
        print(f"PnL: Rs {pnl:,.2f} ({pnl / CAPITAL * 100:.2f}% of Rs {CAPITAL:,.0f} capital)")
    else:
        last_day = trading_days[-1]
        last_prem = premiums_for(last_day)
        try:
            last_debit = sum((last_prem[l.strike][l.option_type] if l.side == "SHORT" else
                              -last_prem[l.strike][l.option_type]) for l in legs)
            unrealized = (credit - last_debit) * LOT_SIZE
            print(f"Still open as of {last_day} | unrealized PnL: Rs {unrealized:,.2f}")
        except KeyError:
            print(f"Still open as of {last_day} (no premium data for that day)")


if __name__ == "__main__":
    main()

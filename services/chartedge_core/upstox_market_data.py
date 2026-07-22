"""
upstox_market_data.py
----------------------
Raw Upstox v2 market-data REST calls (LTP, option chain, expiry
enumeration) used by UpstoxDataProvider (positional_data_provider.py).
Kept separate from upstox_broker.py -- that module is order-execution
only (place/GTT/funds); this one is read-only market data. Same
conventions as upstox_broker.py: never raise to the caller, return
None/[] on any failure, `.text[:200]` truncation on HTTP errors.

⚠️ Endpoint paths/params below are the commonly-documented Upstox v2
shape (as of this writing) -- verify against current Upstox API docs
before relying on this in production; Upstox has reshuffled v2/v3 paths
before (see upstox_broker.py's UPSTOX_GTT_BASE v2/v3 split).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

import requests

from services.chartedge_core.upstox_broker import UPSTOX_API_BASE

LTP_PATH = "/market-quote/ltp"
OPTION_CHAIN_PATH = "/option/chain"
OPTION_CONTRACT_PATH = "/option/contract"
HOLDINGS_PATH = "/portfolio/long-term-holdings"


def _api_base(broker) -> str:
    return broker._api_base if broker is not None else UPSTOX_API_BASE


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def fetch_holdings(broker, token: str) -> list[dict[str, Any]]:
    """Actual CNC delivery holdings from Upstox -- the broker's real book,
    independent of whatever the app's in-memory/DB position state says.
    Used by the positional-stocks reconciliation job to correct DB drift
    (e.g. a BUY that never actually filled -- funds/RMS block -- but got
    committed to the paper record before the live order result came back).
    Returns [] on any HTTP/parse failure, same convention as fetch_ltp."""
    try:
        resp = requests.get(
            f"{_api_base(broker)}{HOLDINGS_PATH}",
            headers=_headers(token), timeout=15,
        )
        if resp.status_code != 200:
            print(f"⚠️ [Positional/Upstox] holdings HTTP {resp.status_code}: {resp.text[:200]}")
            return []
        return resp.json().get("data", []) or []
    except Exception as e:
        print(f"⚠️ [Positional/Upstox] holdings fetch failed: {e}")
        return []


def fetch_ltp(broker, token: str, instrument_key: str) -> Optional[float]:
    """Last-traded price for a single instrument key (index or option leg).
    Returns None on any HTTP/parse failure -- caller decides the fallback
    (e.g. UpstoxDataProvider.get_vix() falls back to 15.0)."""
    try:
        resp = requests.get(
            f"{_api_base(broker)}{LTP_PATH}",
            headers=_headers(token), params={"instrument_key": instrument_key}, timeout=15,
        )
        if resp.status_code != 200:
            print(f"⚠️ [Positional/Upstox] LTP HTTP {resp.status_code} for {instrument_key}: {resp.text[:200]}")
            return None
        data = resp.json().get("data", {}) or {}
        # Upstox keys the response by a normalized instrument-key variant
        # (colon vs pipe, spacing); fall back to the single-entry value if
        # the exact key doesn't match rather than guessing further.
        row = data.get(instrument_key)
        if row is None and len(data) == 1:
            row = next(iter(data.values()))
        if row is None:
            return None
        price = row.get("last_price")
        return float(price) if price is not None else None
    except Exception as e:
        print(f"⚠️ [Positional/Upstox] LTP fetch failed for {instrument_key}: {e}")
        return None


def _fetch_expiries(broker, token: str, index_key: str) -> list[date]:
    """Enumerate available NIFTY option expiries via the option/contract
    endpoint. Returns [] on any failure -- caller falls back to computing
    the next weekly expiry itself, same as the INDstocks path did."""
    try:
        resp = requests.get(
            f"{_api_base(broker)}{OPTION_CONTRACT_PATH}",
            headers=_headers(token), params={"instrument_key": index_key}, timeout=15,
        )
        if resp.status_code != 200:
            print(f"⚠️ [Positional/Upstox] contract HTTP {resp.status_code}: {resp.text[:200]}")
            return []
        rows = resp.json().get("data", []) or []
        expiries = set()
        for row in rows:
            exp = row.get("expiry")
            if exp:
                try:
                    expiries.add(datetime.strptime(exp, "%Y-%m-%d").date())
                except ValueError:
                    continue
        return sorted(expiries)
    except Exception as e:
        print(f"⚠️ [Positional/Upstox] contract fetch failed: {e}")
        return []


def fetch_option_chain(broker, token: str, index_key: str, current_dt: datetime,
                        expiry_buffer_days: int = 0) -> list[dict]:
    """Fetch the near-week NIFTY option chain, pre-annotated with each leg's
    LTP (Upstox returns it inline -- unlike INDstocks, no separate per-leg
    call is needed). Picks the nearest expiry >= current_dt.date() +
    expiry_buffer_days, mirroring positional_runtime.py's prior
    expiries_seen[0] logic. Returns [] on any failure."""
    expiries = _fetch_expiries(broker, token, index_key)
    if not expiries:
        return []
    today = current_dt.date() if hasattr(current_dt, "date") else current_dt
    target_expiry = None
    for exp in expiries:
        if (exp - today).days >= expiry_buffer_days:
            target_expiry = exp
            break
    if target_expiry is None:
        return []

    try:
        resp = requests.get(
            f"{_api_base(broker)}{OPTION_CHAIN_PATH}",
            headers=_headers(token),
            params={"instrument_key": index_key, "expiry_date": target_expiry.strftime("%Y-%m-%d")},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"⚠️ [Positional/Upstox] option chain HTTP {resp.status_code}: {resp.text[:200]}")
            return []
        rows = resp.json().get("data", []) or []
    except Exception as e:
        print(f"⚠️ [Positional/Upstox] option chain fetch failed: {e}")
        return []

    chain: list[dict] = []
    for row in rows:
        strike = row.get("strike_price")
        if strike is None:
            continue
        ce = row.get("call_options") or {}
        pe = row.get("put_options") or {}
        ce_market = ce.get("market_data") or {}
        pe_market = pe.get("market_data") or {}
        chain.append({
            "strike": float(strike),
            "expiry": target_expiry.strftime("%Y-%m-%d"),
            "ce_token": ce.get("instrument_key", ""),
            "pe_token": pe.get("instrument_key", ""),
            "ce_ltp": float(ce_market["ltp"]) if ce_market.get("ltp") is not None else None,
            "pe_ltp": float(pe_market["ltp"]) if pe_market.get("ltp") is not None else None,
        })
    return chain

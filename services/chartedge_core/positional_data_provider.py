"""
positional_data_provider.py
----------------------------
Market-data source for the weekly positional options module
(positional_runtime.py / positional_trading.py). Upstox-only in
production (api.py hardcodes UpstoxDataProvider): REST calls sourcing
NIFTY spot/VIX/option chain/leg LTP from Upstox v2
(services/chartedge_core/upstox_market_data.py), reusing the same
same-day WhatsApp/app token-approval flow already wired for order
execution in upstox_broker.py -- no separate manual token chore for
this module.

IndstocksDataProvider below is kept dormant for future use only -- not
imported or instantiated anywhere currently.

Chain-row shape is {"strike", "expiry", "ce_token", "pe_token"} and
premiums shape is {strike: {"CE": ltp, "PE": ltp}}; neither ever reaches
PositionalTradingEngine, which only ever sees strike-keyed premium dicts
(plain floats).

Methods are async (not sync) even though the implementation is a
blocking REST call under the hood -- matches positional_runtime.py's
existing async call site, and lets UpstoxDataProvider await the shared
Telegram token-needed alert (upstox_broker.notify_token_needed) inline
instead of a fire-and-forget hack.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

import httpx
from zoneinfo import ZoneInfo

from services.chartedge_core.positional_trading import Leg

IST = ZoneInfo("Asia/Kolkata")


class MarketDataProvider(ABC):
    """Minimal interface positional_runtime.py needs to run a check: spot,
    VIX, option chain, and leg LTPs. Implementations must never raise --
    return None/{}/[] on any failure so callers keep their existing
    "skip today's check" behavior."""

    @abstractmethod
    async def get_spot(self, symbol: str = "NIFTY") -> Optional[float]: ...

    @abstractmethod
    async def get_vix(self) -> float: ...

    @abstractmethod
    async def get_option_chain(self, spot: float, symbol: str, range_strikes: int,
                                current_dt: datetime, expiry_buffer_days: int = 0) -> list[dict]: ...

    @abstractmethod
    async def get_leg_premiums(self, chain: list[dict], legs: list[Leg]) -> dict[float, dict[str, float]]: ...


# Kept for future use only -- NOT wired into api.py (weekly positional is
# Upstox-only by design, see api.py:44). Re-enable by importing this class
# there and branching on config.positional_risk.data_source again if a
# reason to fall back to INDstocks ever comes up.
def _fetch_indstocks_ltp(base_url: str, token: str, scrip_code: str) -> Optional[float]:
    """One-off REST quote via the last 1-min historical candle (INDstocks has
    no dedicated LTP endpoint; reuses the same historical API the intraday
    engine already relies on)."""
    try:
        now = datetime.now(IST)
        start = now.replace(hour=9, minute=15, second=0, microsecond=0)
        resp = httpx.get(
            f"{base_url}/market/historical/1minute",
            headers={"Authorization": token},
            params={
                "scrip-codes": scrip_code,
                "start_time": int(start.timestamp() * 1000),
                "end_time": int(now.timestamp() * 1000),
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        rows = resp.json().get("data", []) or resp.json().get(scrip_code, [])
        if not rows:
            return None
        return float(rows[-1].get("close") or rows[-1][4])
    except Exception as e:
        print(f"⚠️ [Positional/INDstocks] LTP fetch failed for {scrip_code}: {e}")
        return None


class IndstocksDataProvider(MarketDataProvider):
    """Thin wrapper over the existing IndstocksMarketRuntime + DerivativeManager.
    Kept for future use -- not currently instantiated anywhere (api.py hardcodes
    UpstoxDataProvider). All blocking calls, wrapped in async defs to satisfy
    the interface."""

    def __init__(self, market_runtime):
        self.market_runtime = market_runtime

    async def get_spot(self, symbol: str = "NIFTY") -> Optional[float]:
        try:
            return self.market_runtime.candles[symbol][-1].close
        except (KeyError, IndexError):
            return None

    async def get_vix(self) -> float:
        try:
            return self.market_runtime.candles["INDIAVIX"][-1].close
        except (KeyError, IndexError):
            return 15.0

    async def get_option_chain(self, spot: float, symbol: str, range_strikes: int,
                                current_dt: datetime, expiry_buffer_days: int = 0) -> list[dict]:
        dm = getattr(self.market_runtime, "dm", None)
        if dm is None:
            return []
        return dm.get_option_chain(spot, symbol, range_strikes=range_strikes,
                                    current_dt=current_dt, expiry_buffer_days=expiry_buffer_days)

    async def get_leg_premiums(self, chain: list[dict], legs: list[Leg]) -> dict[float, dict[str, float]]:
        base_url = self.market_runtime.indstocks["base_url"]
        token = os.getenv("INDMONEY_TOKEN", "")
        strike_set = {leg.strike for leg in legs}
        premiums: dict[float, dict[str, float]] = {}
        for row in chain:
            strike = row.get("strike")
            if strike not in strike_set:
                continue
            entry = {}
            ce_token = row.get("ce_token", "").split(":")[-1]
            pe_token = row.get("pe_token", "").split(":")[-1]
            if ce_token:
                ltp = _fetch_indstocks_ltp(base_url, token, ce_token)
                if ltp is not None:
                    entry["CE"] = ltp
            if pe_token:
                ltp = _fetch_indstocks_ltp(base_url, token, pe_token)
                if ltp is not None:
                    entry["PE"] = ltp
            if entry:
                premiums[strike] = entry
        return premiums


class UpstoxDataProvider(MarketDataProvider):
    """REST-only provider sourcing NIFTY spot/VIX/option chain/premiums from
    Upstox v2 (services/chartedge_core/upstox_market_data.py). No
    market_runtime dependency. Never raises -- returns None/{}/[] on any
    token/HTTP failure so the caller's existing "skip today" guards fire
    unchanged. Does not store a broker reference at construction: calls
    live_broker() fresh on each use (same convention as
    positional_stocks_runtime.py's _live_entry/_live_exit) so it always
    sees the fully-configured singleton regardless of import order."""

    def __init__(self, index_key: str = "NSE_INDEX|Nifty 50", vix_key: str = "NSE_INDEX|India VIX"):
        self.index_key = index_key
        self.vix_key = vix_key

    def _broker(self):
        from services.chartedge_core.upstox_broker import live_broker
        return live_broker()

    async def _ensure_token(self) -> Optional[str]:
        broker = self._broker()
        token = broker.get_valid_token()
        if token:
            return token
        from services.chartedge_core.upstox_broker import notify_token_needed
        await notify_token_needed(reason="weekly positional data fetch")
        return None

    async def get_spot(self, symbol: str = "NIFTY") -> Optional[float]:
        token = await self._ensure_token()
        if not token:
            return None
        from services.chartedge_core.upstox_market_data import fetch_ltp
        return fetch_ltp(self._broker(), token, self.index_key)

    async def get_vix(self) -> float:
        token = await self._ensure_token()
        if not token:
            return 15.0
        from services.chartedge_core.upstox_market_data import fetch_ltp
        vix = fetch_ltp(self._broker(), token, self.vix_key)
        return vix if vix is not None else 15.0

    async def get_option_chain(self, spot: float, symbol: str, range_strikes: int,
                                current_dt: datetime, expiry_buffer_days: int = 0) -> list[dict]:
        token = await self._ensure_token()
        if not token:
            return []
        from services.chartedge_core.upstox_market_data import fetch_option_chain
        return fetch_option_chain(self._broker(), token, self.index_key, current_dt, expiry_buffer_days) or []

    async def get_leg_premiums(self, chain: list[dict], legs: list[Leg]) -> dict[float, dict[str, float]]:
        # Upstox's /v2/option/chain response already carries each leg's LTP
        # inline (see upstox_market_data.fetch_option_chain), so no extra
        # per-leg REST calls are needed -- unlike INDstocks' historical-candle
        # hack, chain rows here are pre-annotated with "ce_ltp"/"pe_ltp".
        strike_set = {leg.strike for leg in legs}
        premiums: dict[float, dict[str, float]] = {}
        for row in chain:
            strike = row.get("strike")
            if strike not in strike_set:
                continue
            entry = {}
            if row.get("ce_ltp") is not None:
                entry["CE"] = row["ce_ltp"]
            if row.get("pe_ltp") is not None:
                entry["PE"] = row["pe_ltp"]
            if entry:
                premiums[strike] = entry
        return premiums

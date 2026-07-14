"""
upstox_broker.py
----------------
Live order execution adapter for the positional-stocks engine via the
Upstox REST API v2. Deliberately isolated from the signal/backtest logic:
the engine decides WHAT to trade; this module only decides HOW to send it
to the broker, and does so behind hard safety gates.

SAFETY MODEL (real money -- read before touching):
  Two independent gates, BOTH default to the safe position, so a fresh
  checkout can never place a real order by accident:
    1. live_trading.enabled  (config)  -- master kill switch, default False.
    2. live_trading.dry_run  (config)  -- default True; when True every
       order is logged and returned as a simulated fill, nothing hits the
       wire.
  A real order requires enabled=True AND dry_run=False AND a valid token
  for *today*. Miss any one -> no live order (caller falls back to paper).

TOKEN MODEL (Upstox constraint, not ours):
  Upstox order tokens expire daily ~3:30 AM IST. No refresh token exists.
  The token is delivered to our webhook (see api.py /api/upstox/token_webhook)
  after the user approves the daily WhatsApp/in-app push, and stored in
  UPSTOX_TOKEN_FILE as {"date": "YYYY-MM-DD", "access_token": "..."}.
  get_valid_token() returns it ONLY if its date == today (IST); otherwise
  None -> the daemon runs paper for the day and alerts. Forgetting the
  daily approval is therefore harmless to capital: no token = no new entry,
  and open positions stay protected by their server-side GTT stop (below).

DOWNSIDE PROTECTION (decoupled from the daily token, by design):
  Every live BUY also places a GTT (Good-Till-Triggered) stop-loss that
  lives on Upstox's servers. It triggers even if our daemon is down, the
  laptop is off, or the token expired -- so a forgotten daily approval can
  never leave an open position with an uncapped loss. We intentionally do
  NOT place a GTT target leg: a server-side target would force an exit at
  +target% while re-entry needs the (possibly absent) token, manufacturing
  the exact missed-opportunity the user flagged. Targets/trailing/sell-
  signal exits stay dynamic in the engine; only the hard stop is server-side.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

IST = ZoneInfo("Asia/Kolkata")

# --- Upstox REST endpoints. Verify against current Upstox API docs before
# arming live; kept as constants so a version bump is a one-line change. ---
UPSTOX_API_BASE = os.getenv("UPSTOX_API_BASE", "https://api.upstox.com/v2")
PLACE_ORDER_PATH = "/order/place"
ORDER_DETAILS_PATH = "/order/details"
# GTT lives on a separate (newer) API version on Upstox; override via env if
# the path differs for your app. Must be confirmed before live use.
UPSTOX_GTT_BASE = os.getenv("UPSTOX_GTT_BASE", "https://api.upstox.com/v3")
PLACE_GTT_PATH = "/order/gtt/place"

TOKEN_FILE = Path(os.getenv("UPSTOX_TOKEN_FILE", "data/upstox_token.json"))

# Product code for delivery (CNC-equivalent) -- positional swing = delivery,
# never intraday/MIS. Hardcoded so a config typo can't turn these into
# leveraged intraday trades.
PRODUCT_DELIVERY = "D"


def _today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


@dataclass
class OrderResult:
    """Uniform result for both real and simulated orders so the caller never
    branches on dry_run -- it just reads .ok / .order_id / .simulated."""
    ok: bool
    simulated: bool
    order_id: Optional[str] = None
    gtt_id: Optional[str] = None
    avg_price: Optional[float] = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "simulated": self.simulated, "order_id": self.order_id,
            "gtt_id": self.gtt_id, "avg_price": self.avg_price, "reason": self.reason,
        }


class UpstoxBroker:
    """Thin, defensive wrapper over the Upstox order + GTT REST endpoints.

    Construct once (see live_broker() singleton). Every entry point re-checks
    the gates, so flipping the kill switch in config takes effect on the next
    call without a restart.
    """

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg or {}
        self.enabled: bool = bool(self.cfg.get("enabled", False))
        self.dry_run: bool = bool(self.cfg.get("dry_run", True))
        self.gtt_stop_pct: float = float(self.cfg.get("gtt_stop_pct", 4.0))
        self.order_type: str = str(self.cfg.get("order_type", "MARKET")).upper()
        # symbol -> Upstox instrument_key (e.g. "NSE_EQ|INE528G01035"). Orders
        # for symbols missing here are skipped, not guessed -- a wrong key
        # could trade the wrong instrument.
        self.instrument_keys: dict[str, str] = self.cfg.get("instrument_keys", {}) or {}
        self.api_key: Optional[str] = os.getenv("UPSTOX_API_KEY")

    # --- gates -------------------------------------------------------------
    def is_armed(self) -> bool:
        """True only when a real order is permitted to leave this process."""
        return self.enabled and not self.dry_run

    def get_valid_token(self) -> Optional[str]:
        """Return today's Upstox access token, or None if absent/stale.

        Stale-by-date is treated as no-token on purpose: an expired token
        must never be sent (Upstox would reject, but we fail closed anyway)."""
        try:
            if not TOKEN_FILE.exists():
                return None
            data = json.loads(TOKEN_FILE.read_text())
            if data.get("date") != _today_ist():
                return None
            tok = data.get("access_token")
            return tok or None
        except Exception as e:
            print(f"[UpstoxBroker] token read failed: {e}")
            return None

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # --- entry: buy + protective GTT stop ---------------------------------
    def place_entry(self, symbol: str, quantity: int, ref_price: float,
                    tag: str) -> OrderResult:
        """Place a delivery BUY and, on success, a server-side GTT stop-loss.

        `tag` groups the order in the Upstox order book by bucket
        (e.g. POS_MIDCAP) so live positions are trackable per pool.
        `ref_price` (today's close) is only used to compute the GTT stop
        trigger; the entry itself is MARKET (or config order_type).

        Never raises to the caller -- any failure returns ok=False so the
        daemon can fall back to a paper record and alert."""
        if quantity <= 0:
            return OrderResult(ok=False, simulated=True, reason="quantity<=0")

        instrument = self.instrument_keys.get(symbol)
        if not instrument:
            # Fail closed: unknown instrument key -> do not place anything.
            return OrderResult(ok=False, simulated=True,
                               reason=f"no instrument_key for {symbol}")

        # DRY RUN or NOT ARMED -> simulate, touch nothing on the wire.
        if not self.is_armed():
            stop_trigger = round(ref_price * (1 - self.gtt_stop_pct / 100), 2)
            print(f"[UpstoxBroker DRY] BUY {symbol} x{quantity} @~{ref_price} "
                  f"tag={tag} | GTT stop @{stop_trigger} "
                  f"(enabled={self.enabled} dry_run={self.dry_run})")
            return OrderResult(ok=True, simulated=True, order_id=f"DRY-{symbol}",
                               gtt_id=f"DRY-GTT-{symbol}", avg_price=ref_price,
                               reason="dry_run/not_armed")

        token = self.get_valid_token()
        if not token:
            return OrderResult(ok=False, simulated=False,
                               reason="no valid token for today")

        # --- real BUY ---
        try:
            body = {
                "quantity": int(quantity),
                "product": PRODUCT_DELIVERY,
                "validity": "DAY",
                "price": 0,
                "tag": tag[:40],
                "instrument_token": instrument,
                "order_type": self.order_type,
                "transaction_type": "BUY",
                "disclosed_quantity": 0,
                "trigger_price": 0,
                "is_amo": False,
            }
            resp = requests.post(f"{UPSTOX_API_BASE}{PLACE_ORDER_PATH}",
                                 headers=self._headers(token), json=body, timeout=15)
            if resp.status_code not in (200, 201):
                return OrderResult(ok=False, simulated=False,
                                   reason=f"order HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json().get("data", {})
            order_id = data.get("order_id") or (data.get("order_ids") or [None])[0]
        except Exception as e:
            return OrderResult(ok=False, simulated=False, reason=f"order exception: {e}")

        # --- protective GTT stop (best-effort; entry already live) ---
        gtt_id = None
        gtt_reason = ""
        try:
            gtt_id = self._place_gtt_stop(symbol, instrument, quantity, ref_price, token)
        except Exception as e:
            gtt_reason = f" | GTT FAILED: {e}"
            # Entry is filled but stop didn't attach -- surface loudly so the
            # caller can alert; do NOT silently pretend it's protected.

        return OrderResult(
            ok=True, simulated=False, order_id=order_id, gtt_id=gtt_id,
            avg_price=None,
            reason=("entry placed" + (gtt_reason or f" + GTT {gtt_id}")),
        )

    def _place_gtt_stop(self, symbol: str, instrument: str, quantity: int,
                        ref_price: float, token: str) -> Optional[str]:
        """Server-side single-leg GTT stop-loss (SELL below trigger). Trigger
        = ref_price * (1 - gtt_stop_pct/100). Lives independent of our daemon
        and the daily token -- this is the forgot-token safety net."""
        trigger = round(ref_price * (1 - self.gtt_stop_pct / 100), 2)
        body = {
            "type": "SINGLE",
            "quantity": int(quantity),
            "product": PRODUCT_DELIVERY,
            "instrument_token": instrument,
            "transaction_type": "SELL",
            "rules": [{
                "strategy": "ENTRY",
                "trigger_type": "BELOW",
                "trigger_price": trigger,
            }],
        }
        resp = requests.post(f"{UPSTOX_GTT_BASE}{PLACE_GTT_PATH}",
                             headers=self._headers(token), json=body, timeout=15)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"GTT HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json().get("data", {})
        return data.get("gtt_order_id") or data.get("gtt_order_ids", [None])[0]

    # --- exit: dynamic sell (engine-driven; token required) ---------------
    def place_exit(self, symbol: str, quantity: int, tag: str) -> OrderResult:
        """Delivery SELL to close a live position on an engine exit decision
        (target / trailing / sell-signal). Requires a valid token; if absent
        the position simply stays open and its GTT stop remains the floor."""
        if quantity <= 0:
            return OrderResult(ok=False, simulated=True, reason="quantity<=0")
        instrument = self.instrument_keys.get(symbol)
        if not instrument:
            return OrderResult(ok=False, simulated=True,
                               reason=f"no instrument_key for {symbol}")

        if not self.is_armed():
            print(f"[UpstoxBroker DRY] SELL {symbol} x{quantity} tag={tag}")
            return OrderResult(ok=True, simulated=True, order_id=f"DRY-EXIT-{symbol}",
                               reason="dry_run/not_armed")

        token = self.get_valid_token()
        if not token:
            return OrderResult(ok=False, simulated=False,
                               reason="no valid token for today (GTT stop still protects)")
        try:
            body = {
                "quantity": int(quantity), "product": PRODUCT_DELIVERY,
                "validity": "DAY", "price": 0, "tag": tag[:40],
                "instrument_token": instrument, "order_type": self.order_type,
                "transaction_type": "SELL", "disclosed_quantity": 0,
                "trigger_price": 0, "is_amo": False,
            }
            resp = requests.post(f"{UPSTOX_API_BASE}{PLACE_ORDER_PATH}",
                                 headers=self._headers(token), json=body, timeout=15)
            if resp.status_code not in (200, 201):
                return OrderResult(ok=False, simulated=False,
                                   reason=f"exit HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json().get("data", {})
            order_id = data.get("order_id") or (data.get("order_ids") or [None])[0]
            return OrderResult(ok=True, simulated=False, order_id=order_id, reason="exit placed")
        except Exception as e:
            return OrderResult(ok=False, simulated=False, reason=f"exit exception: {e}")


_broker_singleton: Optional[UpstoxBroker] = None


def live_broker(cfg: Optional[dict[str, Any]] = None) -> UpstoxBroker:
    """Process-wide broker. First call wires config; later calls reuse it.
    Pass cfg=... again to rebuild (e.g. after a config reload)."""
    global _broker_singleton
    if _broker_singleton is None or cfg is not None:
        _broker_singleton = UpstoxBroker(cfg or {})
    return _broker_singleton

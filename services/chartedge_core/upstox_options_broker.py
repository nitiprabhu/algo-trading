"""
upstox_options_broker.py
------------------------
Live multi-leg F&O order execution for the weekly positional options module
(positional_runtime.py / positional_trading.py) via the Upstox REST API.
Deliberately separate from upstox_broker.py (single-leg equity delivery for
the positional-stocks pools) -- options need basket placement, F&O margin
checks, and unwind-on-partial-fill, none of which apply to delivery buys.

SAFETY MODEL (real money -- read before touching):
  Same two-gate pattern as upstox_broker.py, but under its OWN config key
  (shared/config.yaml positional_risk.live_trading), so arming the stock
  pools can never arm options and vice versa:
    1. positional_risk.live_trading.enabled  -- master switch, default False.
    2. positional_risk.live_trading.dry_run  -- default True; simulate all.
  A real order additionally requires today's Upstox token (shared daily
  WhatsApp-approval flow via upstox_broker.live_broker()).

COMMON-POOL FUNDS (user requirement, 2026-07-20):
  The Upstox account balance is SHARED with the live positional-stocks
  pools. Before placing, we query the REAL free balance and the REAL
  required margin for the 4-leg basket from Upstox (never assume the
  configured capital slice is free), and skip the week if margin doesn't
  fit. Fail closed on any API error.

BASKET ORDERING (never-naked invariant):
  Entry: BUY legs (wings) placed first, SELL legs (shorts) after -- at no
  point mid-sequence is the account short without its hedge, and buying
  wings first also earns the margin benefit for the shorts.
  Exit: BUY-back legs (closing shorts) first, SELL (closing wings) after --
  the mirror invariant.
  Any leg failing mid-basket -> immediately unwind the legs already placed
  (reverse orders), alert, and report ok=False. No half-condor is ever
  intentionally left open; if the unwind itself also fails, that is
  surfaced loudly for manual intervention.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import requests

from services.chartedge_core.upstox_broker import (
    PLACE_ORDER_PATH,
    PRODUCT_DELIVERY,  # "D" = carry-forward (NRML-equivalent) for F&O too
    live_broker,
)

MARGIN_PATH = "/charges/margin"

# Refuse to place if required margin exceeds this fraction of free balance --
# leaves headroom for MTM swings and the stock pools sharing the same funds.
MARGIN_HEADROOM_FRAC = 0.95


@dataclass
class LegOrder:
    """One leg of a basket, fully resolved to an Upstox instrument."""
    instrument_key: str            # e.g. "NSE_FO|54321"
    transaction_type: str          # "BUY" | "SELL"
    quantity: int                  # contracts (lot-size multiples)
    label: str = ""                # human tag, e.g. "SHORT CE 24800"
    order_id: Optional[str] = None # filled in after placement


@dataclass
class BasketResult:
    ok: bool
    simulated: bool
    legs: list[LegOrder] = field(default_factory=list)
    reason: str = ""

    def summary(self) -> str:
        placed = ", ".join(f"{l.label or l.instrument_key}={l.order_id}" for l in self.legs if l.order_id)
        return f"{self.reason}" + (f" [{placed}]" if placed else "")


class UpstoxOptionsBroker:
    """Basket order execution for weekly NIFTY option structures.

    Construct per call via options_broker(cfg) -- reads gates fresh from the
    positional_risk.live_trading config dict so a config change takes effect
    without restart. Token/api-base come from the shared upstox_broker
    singleton (same account, same daily token)."""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg or {}
        self.enabled: bool = bool(self.cfg.get("enabled", False))
        self.dry_run: bool = bool(self.cfg.get("dry_run", True))
        # max margin this module may consume, independent of what the common
        # pool could afford -- keeps options from starving the stock pools.
        self.max_margin: float = float(self.cfg.get("max_margin", 100000.0))
        self._equity_broker = live_broker()
        self._api_base = self._equity_broker._api_base

    def is_armed(self) -> bool:
        return self.enabled and not self.dry_run

    def get_token(self) -> Optional[str]:
        return self._equity_broker.get_valid_token()

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # --- funds/margin gate (common pool) ----------------------------------
    def required_margin(self, token: str, legs: list[LegOrder]) -> Optional[float]:
        """Basket margin from Upstox POST /charges/margin. None on any
        failure -- caller must fail closed (skip the trade), never guess."""
        try:
            body = {"instruments": [
                {
                    "instrument_key": l.instrument_key,
                    "quantity": int(l.quantity),
                    "transaction_type": l.transaction_type,
                    "product": PRODUCT_DELIVERY,
                } for l in legs
            ]}
            resp = requests.post(f"{self._api_base}{MARGIN_PATH}",
                                 headers=self._headers(token), json=body, timeout=15)
            if resp.status_code != 200:
                print(f"[OptionsBroker] margin HTTP {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json().get("data") or {}
            req = data.get("required_margin", data.get("final_margin"))
            return float(req) if req is not None else None
        except Exception as e:
            print(f"[OptionsBroker] margin check failed: {e}")
            return None

    def funds_ok(self, token: str, legs: list[LegOrder]) -> tuple[bool, str]:
        """Common-pool gate: live free balance vs live basket margin.
        Both numbers come from Upstox at call time -- the stock pools share
        this balance, so nothing is assumed from config."""
        margin = self.required_margin(token, legs)
        if margin is None:
            return False, "margin check failed -- not placing blind"
        if margin > self.max_margin:
            return False, (f"required margin ₹{margin:,.0f} exceeds module cap "
                           f"₹{self.max_margin:,.0f}")
        funds = self._equity_broker.get_available_funds(token)
        if funds is None:
            return False, "funds check failed -- not placing blind"
        if margin > funds * MARGIN_HEADROOM_FRAC:
            return False, (f"insufficient common-pool funds: need ₹{margin:,.0f}, "
                           f"free ₹{funds:,.0f} (shared with stock pools)")
        return True, f"margin ₹{margin:,.0f} ok vs free ₹{funds:,.0f}"

    # --- order placement ---------------------------------------------------
    def _place_one(self, token: str, leg: LegOrder, tag: str) -> tuple[bool, str]:
        body = {
            "quantity": int(leg.quantity),
            "product": PRODUCT_DELIVERY,
            "validity": "DAY",
            "price": 0,
            "tag": tag[:40],
            "instrument_token": leg.instrument_key,
            "order_type": "MARKET",
            "transaction_type": leg.transaction_type,
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False,
        }
        try:
            resp = requests.post(f"{self._api_base}{PLACE_ORDER_PATH}",
                                 headers=self._headers(token), json=body, timeout=15)
            if resp.status_code not in (200, 201):
                return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
            data = resp.json().get("data", {})
            leg.order_id = data.get("order_id") or (data.get("order_ids") or [None])[0]
            return True, "placed"
        except Exception as e:
            return False, f"exception: {e}"

    def _unwind(self, token: str, placed: list[LegOrder], tag: str) -> list[str]:
        """Reverse already-placed legs after a mid-basket failure. Returns
        list of legs that could NOT be unwound (needs manual action)."""
        failed: list[str] = []
        for leg in placed:
            reverse = LegOrder(
                instrument_key=leg.instrument_key,
                transaction_type="SELL" if leg.transaction_type == "BUY" else "BUY",
                quantity=leg.quantity,
                label=f"UNWIND {leg.label}",
            )
            ok, why = self._place_one(token, reverse, f"{tag}-UNWIND")
            if not ok:
                failed.append(f"{leg.label or leg.instrument_key}: {why}")
        return failed

    def place_basket(self, legs: list[LegOrder], tag: str) -> BasketResult:
        """Entry basket. BUY legs first (never-naked invariant), then SELL.
        Partial failure -> unwind everything placed so far."""
        if not legs:
            return BasketResult(ok=False, simulated=True, reason="no legs")

        ordered = ([l for l in legs if l.transaction_type == "BUY"] +
                   [l for l in legs if l.transaction_type == "SELL"])

        if not self.is_armed():
            for l in ordered:
                print(f"[OptionsBroker DRY] {l.transaction_type} {l.label or l.instrument_key} "
                      f"x{l.quantity} tag={tag} (enabled={self.enabled} dry_run={self.dry_run})")
                l.order_id = f"DRY-{l.transaction_type}-{l.instrument_key.split('|')[-1]}"
            return BasketResult(ok=True, simulated=True, legs=ordered, reason="dry_run/not_armed")

        token = self.get_token()
        if not token:
            return BasketResult(ok=False, simulated=False, reason="no valid token for today")

        ok, gate_reason = self.funds_ok(token, ordered)
        if not ok:
            return BasketResult(ok=False, simulated=False, reason=gate_reason)

        placed: list[LegOrder] = []
        for leg in ordered:
            leg_ok, why = self._place_one(token, leg, tag)
            if not leg_ok:
                stuck = self._unwind(token, placed, tag)
                reason = f"leg {leg.label or leg.instrument_key} failed ({why}); unwound {len(placed) - len(stuck)}/{len(placed)}"
                if stuck:
                    reason += f" | ⚠️ MANUAL ACTION NEEDED, unwind failed for: {'; '.join(stuck)}"
                return BasketResult(ok=False, simulated=False, legs=placed, reason=reason)
            placed.append(leg)
        return BasketResult(ok=True, simulated=False, legs=placed,
                            reason=f"{gate_reason}; all {len(placed)} legs placed")

    def close_basket(self, legs: list[LegOrder], tag: str) -> BasketResult:
        """Exit basket: pass the ORIGINAL entry legs; this reverses each.
        Shorts are bought back first (mirror of the entry invariant)."""
        reversed_legs = [LegOrder(
            instrument_key=l.instrument_key,
            transaction_type="SELL" if l.transaction_type == "BUY" else "BUY",
            quantity=l.quantity,
            label=f"CLOSE {l.label}",
        ) for l in legs]
        # BUY-backs (closing shorts) first -- place_basket's BUY-first order
        # does exactly that for the reversed set.
        return self.place_basket(reversed_legs, tag)


def options_broker(cfg: Optional[dict[str, Any]] = None) -> UpstoxOptionsBroker:
    """Fresh instance per call -- cheap, and re-reads the gates every time so
    flipping positional_risk.live_trading in config takes effect on the next
    check without a restart."""
    return UpstoxOptionsBroker(cfg or {})

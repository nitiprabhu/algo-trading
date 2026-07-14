"""
test_upstox_sandbox.py
----------------------
End-to-end sandbox test of the live order path -- REAL Upstox API calls,
fake fills, NO money. Proves auth, the place-order endpoint, tags, and error
handling against Upstox's sandbox before any production rupee is risked.

Prereqs:
  1. Generate a sandbox app + 30-day token at
     https://account.upstox.com/developer/apps#sandbox
  2. export UPSTOX_SANDBOX_TOKEN=<that token>
  3. (optional) pass --instrument-key; else it resolves the symbol from the
     Upstox public instrument master.

Usage:
  python scripts/test_upstox_sandbox.py --symbol HFCL --qty 1

Notes:
  - Places a tiny (default 1-share) DELIVERY BUY in sandbox, then reads it
    back. Nothing hits a real exchange or real funds.
  - GTT is not in sandbox yet -> the stop leg is EXPECTED to fail; the script
    reports it as a known-pending, not a bug.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.getcwd())
from services.chartedge_core.upstox_broker import UpstoxBroker  # noqa: E402

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


def resolve_key(symbol: str) -> str | None:
    with urllib.request.urlopen(INSTRUMENTS_URL, timeout=60) as resp:
        raw = resp.read()
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        rows = json.load(gz)
    for r in rows:
        if (r.get("segment") == "NSE_EQ" and r.get("instrument_type") == "EQ"
                and (r.get("trading_symbol") or "").upper() == symbol.upper()):
            return r.get("instrument_key")
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="HFCL")
    ap.add_argument("--qty", type=int, default=1)
    ap.add_argument("--instrument-key", default=None)
    args = ap.parse_args()

    if not os.getenv("UPSTOX_SANDBOX_TOKEN"):
        print("ERROR: set UPSTOX_SANDBOX_TOKEN first (30-day sandbox token from "
              "https://account.upstox.com/developer/apps#sandbox)", file=sys.stderr)
        sys.exit(2)

    key = args.instrument_key or resolve_key(args.symbol)
    if not key:
        print(f"ERROR: could not resolve instrument_key for {args.symbol}", file=sys.stderr)
        sys.exit(2)
    print(f"instrument_key: {args.symbol} -> {key}")

    # Armed, sandbox: real API, fake fills, no money.
    broker = UpstoxBroker({
        "enabled": True, "dry_run": False, "sandbox": True,
        "gtt_stop_pct": 4.0, "instrument_keys": {args.symbol: key},
    })
    print(f"armed={broker.is_armed()} sandbox={broker.sandbox} "
          f"token_present={broker.get_valid_token() is not None}")
    print(f"api_base={broker._api_base}")

    # ref_price only feeds the (expected-to-fail-in-sandbox) GTT trigger calc.
    res = broker.place_entry(args.symbol, args.qty, ref_price=200.0, tag="POS_SANDBOX_TEST")
    print("\n=== place_entry result ===")
    print(json.dumps(res.to_dict(), indent=2))
    if res.ok and res.order_id and not str(res.order_id).startswith("DRY"):
        print(f"\nSANDBOX ORDER PLACED (fake fill): order_id={res.order_id}")
        if "GTT FAILED" in res.reason:
            print("GTT leg failed -- EXPECTED, sandbox has no GTT yet.")
    else:
        print("\nOrder did not place -- inspect reason above.")


if __name__ == "__main__":
    main()

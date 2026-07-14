"""
resolve_upstox_keys.py
----------------------
Resolve the positional-stocks symbols -> Upstox instrument_key by matching
against the Upstox instrument master file, and print a ready-to-paste YAML
block for shared/config.yaml `live_trading.instrument_keys`.

Why a separate step: the live broker fails closed on any symbol whose
instrument_key is missing (a wrong key could trade the wrong stock). This
resolves them explicitly, once, so you can eyeball the mapping before it
ever touches real money.

Usage:
    python scripts/resolve_upstox_keys.py

The instrument-master URL is Upstox's public file; verify it against current
Upstox docs if the download 404s (they occasionally move the path).
"""
from __future__ import annotations

import gzip
import io
import json
import sys
import urllib.request

# Upstox public NSE instrument master (equity + others). Verify if it 404s.
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"

# The three positional pools' symbols. Keep in sync with shared/config.yaml.
SYMBOLS = [
    # largecap
    "SBIN", "ADANIENT", "VEDL", "ONGC", "HINDALCO",
    # midcap
    "LAURUSLABS", "M&MFIN", "ASHOKLEY", "IDEA", "ADANIENSOL",
    "MUTHOOTFIN", "FEDERALBNK", "NYKAA",
    # smallcap/microcap
    "BGRENERGY", "STLTECH", "AXISCADES", "HFCL", "NATIONALUM",
    "SOLARINDS", "HINDCOPPER", "DIACABS", "INDIGRID", "APCOTEXIND",
]


def load_instruments() -> list[dict]:
    print(f"Downloading {INSTRUMENTS_URL} ...", file=sys.stderr)
    with urllib.request.urlopen(INSTRUMENTS_URL, timeout=60) as resp:
        raw = resp.read()
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        return json.load(gz)


def main() -> None:
    instruments = load_instruments()
    # Index cash-segment instruments by trading_symbol. Upstox cash segment is
    # "NSE_EQ". Accept the tradeable delivery types:
    #   EQ = normal equity, BE = Trade-to-Trade/surveillance equity (compulsory
    #   delivery, tighter circuits), IV = InvIT (e.g. INDIGRID -- a trust, not a
    #   stock). All three are delivery-tradeable; non-EQ ones are flagged below
    #   so you know what you're actually buying.
    ALLOWED = {"EQ", "BE", "IV"}
    by_symbol: dict[str, tuple[str, str]] = {}
    for row in instruments:
        if row.get("segment") == "NSE_EQ" and row.get("instrument_type") in ALLOWED:
            ts = row.get("trading_symbol") or row.get("tradingsymbol")
            key = row.get("instrument_key")
            if ts and key:
                by_symbol[ts.upper()] = (key, row.get("instrument_type"))

    print("\n# paste under live_trading.instrument_keys in shared/config.yaml")
    print("  instrument_keys:")
    missing = []
    for sym in SYMBOLS:
        hit = by_symbol.get(sym.upper())
        if hit:
            key, itype = hit
            # quote symbols with special chars (e.g. M&MFIN) for valid YAML
            name = f'"{sym}"' if any(c in sym for c in "&:") else sym
            flag = "" if itype == "EQ" else f"  # {itype} -- not normal equity, review"
            print(f"    {name}: \"{key}\"{flag}")
        else:
            missing.append(sym)

    if missing:
        print(f"\n# UNRESOLVED (fix manually before arming live): {missing}", file=sys.stderr)
        sys.exit(1)
    print(f"\n# resolved all {len(SYMBOLS)} symbols", file=sys.stderr)


if __name__ == "__main__":
    main()

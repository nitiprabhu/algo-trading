#!/usr/bin/env python3
"""Quick GARCH VIX band test: May 11-12 backtest with static vs GARCH bands."""

import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services.chartedge_core.config import load_config
from services.chartedge_core.models import Candle
from services.chartedge_core.garch_volatility import compute_garch_forecast
from services.chartedge_core.strategies import EagleNiftyT315
from services.chartedge_core.simulation import MarketSimulator

IST = ZoneInfo("Asia/Kolkata")

def run_backtest(use_garch: bool = False):
    """Run May 11-12 backtest with/without GARCH vol bands."""
    config = load_config()

    sim = MarketSimulator(config, skip_db_load=True)
    sim.is_backtesting = True

    start = datetime(2026, 5, 11, 9, 15, tzinfo=IST)
    end = datetime(2026, 5, 12, 15, 30, tzinfo=IST)

    candles = sim._load_backtest_candles(start, end)
    if not candles:
        print("❌ No candles loaded for May 11-12")
        return

    print(f"📊 Loaded {len(candles)} candles for {start.date()} to {end.date()}")

    eagle = EagleNiftyT315()
    signals = []

    prev_date = None
    daily_candles = []

    for candle in candles:
        if candle.instrument not in ["NIFTY", "BANKNIFTY"]:
            continue

        curr_date = candle.time.date()
        if curr_date != prev_date:
            daily_candles = []
            prev_date = curr_date

        daily_candles.append(candle)

        eagle.update(candle)

        india_vix = 16.5  # Dummy for testing
        vix_band = None

        if use_garch and len(daily_candles) >= 65:
            garch_data = compute_garch_forecast(daily_candles)
            if garch_data:
                vix_band = (garch_data["vix_lower"], garch_data["vix_upper"])
                print(f"[{candle.time}] GARCH VIX band: {vix_band}")

        sig = eagle.get_signal(candle, india_vix, vix_band=vix_band)
        if sig:
            signals.append(sig)
            print(f"✅ Signal: {sig['strategy']} {sig['option_type']} @ {candle.time}")

    print(f"\n{'GARCH' if use_garch else 'Static'}: {len(signals)} signals")
    return len(signals)

if __name__ == "__main__":
    print("="*60)
    print("STATIC VIX BANDS (14-22)")
    print("="*60)
    static_count = run_backtest(use_garch=False)

    print("\n" + "="*60)
    print("GARCH VOL FORECAST BANDS")
    print("="*60)
    garch_count = run_backtest(use_garch=True)

    print(f"\n📈 Signal delta: {garch_count - static_count:+d} ({static_count} → {garch_count})")

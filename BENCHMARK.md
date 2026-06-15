# ChartEdge AI Trading Strategy Benchmark & Rules

This document establishes the locked baseline and performance benchmarks for the F&O trading algorithm. **All future models, agents, and developers must preserve these rules and beat the benchmark below to accept any changes.**

---

## 🔒 1. Locked Risk & Capital Rules (₹2L Capital)

These core safety rules must **never** be removed or bypassed:

1. **Combined Daily Drawdown Pause (Kill Switch)**:
   - Max Daily Loss Limit: **2.5% of total capital (₹5,000)**.
   - Calculation: Combined realized PnL + open PnL for the current day across both Options and Futures.
   - Action: If hit, triggers the global kill switch to force-close all open positions and block new entries for the rest of the day.

2. **Mutual Exclusion (Focus Rule)**:
   - Since 1 lot of Nifty Futures uses ~100% of the ₹2L capital, you cannot trade anything else when a position is open.
   - If a Futures position is active, all Option entries are blocked.
   - If an Options position is active, all Futures entries are blocked.

3. **Position Sizing limits**:
   - **Nifty Options**: Lot size = `75`, Max Lots = `1`.
   - **Nifty Futures**: Lot size = `75`, Max Lots = `1`.

---

## 📊 2. Benchmark Performance (April - June 2026)

To measure the success of any new algorithm, run the full multi-month backtest using:
```bash
python scratch/run_and_notify_backtests_all.py
```

The proposed change **must beat this cumulative baseline** to be accepted:

| Month | Options PnL | Futures PnL | Combined PnL | Trades | Win Rate |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **April 2026** | ₹+1,868.10 | ₹+71,230.27 | **₹+73,098.37** | 23 | 82.6% |
| **May 2026** | ₹-1,984.05 | ₹-16,024.73 | **₹-18,008.78** | 33 | 33.3% |
| **June 2026** (1-15) | ₹-1,730.70 | ₹+16,570.16 | **₹+14,839.46** | 48 | 25.0% |
| **Total Cumulative** | **₹-1,846.65** | **₹+71,775.70** | **₹+69,929.05** | **104** | **38.5%** |

**Target to Beat:** **`₹+69,929.05`** cumulative PnL with maximum drawdown kept under the daily **2.5%** limit.

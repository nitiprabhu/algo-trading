# 📊 Trade Learnings & Post-Mortem Report: May 6th, 2026

## 1. Executive Summary

On **May 6th, 2026**, the ChartEdge AI trading engine executed its live trading session. While the core pipelines for signal confluence, real-time tick ingestion, and ATM options contract resolution functioned correctly, several deep-seated architectural bugs were uncovered. 

Most notably, **open positions failed to square off automatically at 3:00 PM IST (EOD)**, and a **timeline timezone mismatch** led to historical backtests overwriting production database records with negative trade durations. 

This document provides a post-mortem review of today's trades, identifies the root causes of the structural anomalies, and outlines concrete engineering improvements.

---

## 2. Today's Trade Log & Performance Analysis

Below is the consolidated list of trades recorded in the database for **May 6th, 2026**. 

| Symbol | Direction | Entry Time (UTC) | Exit Time (UTC) | Entry Px | Exit Px | Qty | PnL (₹) | Exit Reason |
| :--- | :---: | :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **NIFTY** | BUY | 04:21:19 | 2026-05-04 03:45:00* | 100.05 | 109.95 | 125 | **+1,237.50** | `T2` (Target 2 Hit) |
| **RELIANCE** | BUY | 04:01:00 | 04:31:00 | 1,467.43 | 1,460.37 | 51 | **-360.06** | `SL` (Stop Loss Hit) |
| **HDFCBANK** | BUY | 04:30:00 | 04:36:00 | 777.34 | 774.16 | 96 | **-305.28** | `SL` (Stop Loss Hit) |
| **HDFCBANK** | BUY | 05:00:00 | 09:35:00 | 778.54 | 777.61 | 96 | **-89.28** | `BACKTEST_EOD` |
| **NIFTY-May2026-24150-CE** | BUY | 05:00:00 | 2026-05-04 03:56:00* | 289.78 | 258.57 | 250 | **-7,802.50** | `SL` (Stop Loss Hit) |
| **BANKNIFTY-May2026-55100-CE** | BUY | 05:00:00 | 2026-05-04 03:57:00* | 661.43 | 558.90 | 90 | **-9,227.70** | `SL` (Stop Loss Hit) |
| **NIFTY-May2026-24350-CE** | BUY | 09:15:00 | 10:04:47 | 292.16 | 291.86 | 250 | **-75.00** | `MANUAL_EOD_SQUAREOFF` |
| **BANKNIFTY-May2026-55800-CE** | BUY | 09:15:00 | 10:04:47 | 670.29 | 669.62 | 105 | **-70.35** | `MANUAL_EOD_SQUAREOFF` |

> [!NOTE]
> *Timestamps marked with an asterisk (`*`) exhibit negative trade durations (exiting on May 4th for a trade entered on May 6th). This is due to **Database Pollution via Parallel Backtesting**, detailed in the root cause analysis below.

### Performance Summary
* **Total Trades**: 8
* **Wins**: 1 (12.5% Win Rate)
* **Losses / Flat**: 7 (87.5%)
* **Gross Profit**: ₹1,237.50
* **Gross Loss**: ₹17,930.17
* **Net PnL**: **-₹16,692.67**

---

## 3. What Went GOOD Today?

1. **Successful Option Resolution**: The system correctly identified structural momentum on the indices, resolved the correct ATM weekly Option contracts (`NIFTY-CE` and `BANKNIFTY-CE`), and placed paper orders.
2. **Confluence Filtering**: The confluence scorer correctly integrated weights for RSI, MACD, EMA Ribbon, VWAP, and Supertrend, triggering trades only when the 0.70 threshold was reached.
3. **Manual EOD Mitigation**: Once we realized the automatic square-off was bypassed, our emergency manual EOD square-off script successfully updated the database and closed out overnight risk, avoiding massive open positions during the market close.

---

## 4. What Went WRONG today? (Root Cause Analysis)

### 🐛 Issue A: The Timezone Blindspot in EOD Square-Off
* **Symptom**: Automated EOD square-off at 3:00 PM IST (15:00) did not trigger, leaving multiple positions open after-market.
* **Root Cause**: In [simulation.py](file:///Users/nithish-prabhu/Downloads/intra-day/services/chartedge_core/simulation.py#L181-L187), the EOD boundary check compares the candle's timestamp against the configured `square_off` hour:
  ```python
  sq_hour, sq_min = map(int, self.config.market_hours["square_off"].split(":")) # 15:00
  if candle.time.hour > sq_hour or (candle.time.hour == sq_hour and candle.time.minute >= sq_min):
      # ... trigger square-off ...
  ```
  However, the candles received from the live Indstocks feed are in **naive UTC** (where the market hours run from `03:45` to `10:00` UTC, corresponding to `09:15` to `15:30` IST). 
  Since `candle.time.hour` maxes out at `10` (corresponding to 3:30 PM IST), **it never reaches the configured hour of 15 (3:00 PM IST)**. The automatic EOD logic was entirely blind and never executed.

### 🐛 Issue B: Backtest-Production State Pollution
* **Symptom**: Trades entered on May 6th have exit times recorded as May 4th with negative durations.
* **Root Cause**: The active live trading backend and the backtesting module share the **exact same singleton `trader` instance** and **production database connection** inside FastAPI's runtime. 
  When the user or frontend triggers a backtest (or seeding backfill up to 4 days lookback):
  1. The backtester replays historical candles (e.g., from May 4th).
  2. The shared `trader` evaluates those candles and places orders.
  3. These simulated historical entries/exits are written directly into the **production `traderecord` database table**, overwriting or polluting active live records.
  4. Specifically, a live trade entered on May 6th was updated with an exit time from a May 4th candle that replayed during a concurrent seeding/backtest process.

### 🐛 Issue C: Stale Trades Replay-activation on Restart
* **Symptom**: A server restart causes trades to immediately exit on ancient candles.
* **Root Cause**: When the server restarts, `load_active_trades` in [paper_trading.py](file:///Users/nithish-prabhu/Downloads/intra-day/services/chartedge_core/paper_trading.py#L25) queries the database for all `status = 'OPEN'` rows to resume tracking. 
  Because unclosed trades from prior days were left open (due to Issue A), they were loaded into active memory. During the startup seeding (which replays historical warm-up candles), those stale trades were matched against warm-up candles from 2 days ago, immediately triggering SL or target violations on ancient prices.

### 🐛 Issue D: Option Premium Sizing Volatility
* **Symptom**: Option trades suffered heavy absolute drawdowns (e.g., BANKNIFTYCE loss of -₹9,227).
* **Root Cause**: Options contracts are highly volatile. When sizing trades based on a 2% risk rule of a 5L total capital buffer (₹10,000 max risk per trade), the position size is calculated using `risk_per_trade / risk_per_share`. 
  Because the option premium's stop loss is narrow relative to its underlying spot index, the system allocates a very large quantity (e.g., 250 qty Nifty CE). A small tick move in options premium quickly leads to a large absolute PnL drop close to the maximum ₹10,000 threshold.

---

## 5. Required Architectural Improvements

To bring this automated trading system to institutional grade, we must deploy the following critical patches:

### 🛠️ Improvement 1: Coordinate/Unify Timezones
Convert all incoming naive candle timestamps to the configured timezone (IST) prior to executing any time-bound filters or boundary checks:
```python
# Convert naive UTC candle time to IST before comparison
candle_time_ist = candle.time.astimezone(IST) if candle.time.tzinfo else candle.time.replace(tzinfo=ZoneInfo("UTC")).astimezone(IST)
if candle_time_ist.hour > sq_hour or (candle_time_ist.hour == sq_hour and candle_time_ist.minute >= sq_min):
    # This will now trigger correctly at 3:00 PM IST (15:00)!
```

### 🛠️ Improvement 2: Strict Isolation of Backtesting Environment
Prevent backtest or historical replay sessions from ever calling the production `persist_trade_entry` or `persist_trade_exit` endpoints.
* **Action**: Inject an `is_backtesting` flag into the `PaperTradingEngine`. If active, bypass all database operations and write ONLY to an in-memory backtest reporter.
* **API Isolation**: Ensure the API singleton `/api/backtest` instantiates a detached, isolated `MarketSimulator` instance instead of using the main `runtime` singleton.

### 🛠️ Improvement 3: Stale Trade Protection on Load
On startup, sanitize the state-recovery queries:
* **Action**: Modify `load_active_trades` to only load open trades whose `trade_date` matches the current system date.
* **Fallback**: Any open trades from a previous day must be flagged as `STALE_EOD_SQUAREOFF` and automatically resolved in the database to prevent them from interacting with new tick data.

### 🛠️ Improvement 4: Options-Specific Position Sizing Guardrails
Add a dedicated leverage dampener for F&O derivative contracts. Instead of allowing full capital allocation on options:
* **Action**: Enforce a maximum lot count (e.g., cap weekly options at 4 lots max) or limit option premium stop-loss distance to reduce high-beta variance.

---

### 📝 Next Steps
We have successfully protected our cash balance today by executing the manual EOD square-off. The immediate priority is applying the **Timezone Unification** and **Backtest isolation** patches to prepare for tomorrow's market session.

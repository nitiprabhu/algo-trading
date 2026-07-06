# Real-Data NIFTY Daily-Swing Futures Backtest — Jul 2024 to Jul 2026 (2 years)

**Data source:** NSE F&O bhavcopy front-month futures (real OHLC/settlement, `data/nse_bhavcopy/`). Daily bars only, no intraday candles used or needed.

**Strategy:** EMA20/EMA50 daily crossover, ATR(14)x2.0 stop-loss, risk-based position sizing (constant rupee risk per trade, same fix as the intraday futures engine), reverse-on-opposite-crossover (always in the market once first signal fires).

## Monthly P/L Summary

| Month | Trades | Wins | Win% | Net PnL |
|---|---|---|---|---|
| 2024-10 | 1 | 1 | 100% | Rs 26,036.25 |
| 2025-04 | 1 | 1 | 100% | Rs 40,342.50 |
| 2025-08 | 1 | 0 | 0% | Rs -32,070.00 |
| 2025-09 | 1 | 0 | 0% | Rs -28,132.50 |
| 2026-01 | 1 | 0 | 0% | Rs -35,475.00 |
| 2026-07 | 1 | 1 | 100% | Rs 6,577.50 |
| **TOTAL** | **6** | **3** | **50%** | **Rs -22,721.25** |

## Per-Trade Detail

| Dir | Entry | Exit | Reason | Entry Px | Exit Px | Qty | PnL |
|---|---|---|---|---|---|---|---|
| SELL | 2024-10-23 | 2025-04-21 | REVERSE | 24482.7 | 24135.5 | 75 | 26036.25 |
| BUY | 2025-04-21 | 2025-08-07 | REVERSE | 24135.5 | 24673.4 | 75 | 40342.50 |
| SELL | 2025-08-07 | 2025-08-20 | SL | 24673.4 | 25101.0 | 75 | -32070.00 |
| BUY | 2025-09-11 | 2025-09-26 | SL | 25104.5 | 24729.4 | 75 | -28132.50 |
| SELL | 2026-01-20 | 2026-02-03 | SL | 25259.2 | 25732.2 | 75 | -35475.00 |
| BUY | 2026-07-02 | 2026-07-03 | EOD_OPEN | 24265.0 | 24352.7 | 75 | 6577.50 |

# ChartEdge AI

ChartEdge AI is a Phase 1 implementation of the NSE intraday technical-analysis PRD in `ChartEdge_AI_PRD_Architecture copy.pages`.

What is built:
- FastAPI backend with typed candle, indicator, signal, and paper-trade models.
- Config-driven NIFTY and BANKNIFTY instruments, confluence thresholds, weights, and risk controls.
- Multi-indicator engine: RSI, MACD, EMA ribbon, VWAP, Supertrend reference, volume, ATR, Bollinger reference.
- Claude-compatible signal engine with deterministic rule-based fallback when `ANTHROPIC_API_KEY` is absent.
- Paper trading engine with confidence floor, one open position per instrument, SL/T1/T2, T1 breakeven trail, and kill switch.
- Next.js dashboard with signal feed, open positions, equity curve, trade log, metrics, and indicator confluence.

## Run Locally

Backend:

```bash
pip install -e ".[dev]"
uvicorn services.chartedge_core.api:app --reload --port 8000
```

Frontend:

```bash
cd frontend
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

Open `http://localhost:3000`.

## Environment

Copy `.env.example` to `.env` and set local secrets there:

```bash
cp .env.example .env
```

Set `ANTHROPIC_API_KEY` to enable live Claude reasoning. Without it, signals are still generated from the confluence engine and marked `AI_UNAVAILABLE`.

Set `CHARTEDGE_DATA_SOURCE=indstocks` and `INDMONEY_TOKEN` to use INDstocks historical backfill and live price websocket data. Keep `CHARTEDGE_DATA_SOURCE=mock` for local demo mode.

`OPENAI_API_KEY` is optional. The AI brain is provider-swappable via `shared/config.yaml`; `anthropic` is the default provider and `openai` can be selected for comparison.

`INDMONEY_TOKEN` is used here only for INDstocks market data. The current Phase 1 build does not place live orders.

## Next Integration Steps

- Persist candles/signals/trades into TimescaleDB/PostgreSQL using `shared/db/schema.sql`.
- Split the in-process services onto Redis Pub/Sub topics following the PRD event flow.

## 📊 May 11 & May 12, 2026 Index Options Backtest Comparison

We executed a comprehensive options backtest comparison for **NIFTY & BANKNIFTY** options during the trending sessions of May 11 and May 12, 2026. 

To isolate options-only performance, monitor equities (`RELIANCE` and `HDFCBANK`) were kept strictly in **monitor-only** mode.

### 📈 Comparative Results Table

| Metric | Config A: Pure Options (No AI) | Config B: AI Guardrail (Single AI Review) |
| :--- | :---: | :---: |
| **Total Trades** | 4 | 0 |
| **Winning Trades** | 4 | 0 |
| **Losing Trades** | 0 | 0 |
| **Win Rate %** | **100.0%** | **0.0%** |
| **Total Net PnL (₹)** | **₹+18,592.00** | **₹+0.00** |
| **Profit Factor** | 18592.00 | 1.00 |

### 📝 Executed Trades Log (Pure Options - No AI)

| Date | Instrument / Option Contract | Type | Qty | Entry Prem | Exit Prem | Net P&L (₹) | Exit Reason |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **05-11** | `NIFTY-May2026-23900-PE` | BUY | 200 | 286.72 | 289.73 | **₹+602.00** | Theta Mitigation (45m) |
| **05-11** | `BANKNIFTY-May2026-54900-CE` | BUY | 105 | 659.17 | 748.92 | **₹+9,423.75** | Theta Mitigation (45m) |
| **05-12** | `NIFTY-May2026-23600-PE` | BUY | 125 | 283.44 | 290.99 | **₹+943.75** | Theta Mitigation (45m) |
| **05-12** | `NIFTY-May2026-23450-PE` | BUY | 250 | 281.63 | 312.12 | **₹+7,622.50** | EOD Square-Off (15:00) |

### 🔍 Analytical Findings

1. **AI Over-Conservatism Under Volatility**: Because of elevated VIX levels ($>18$), the AI single review system acted conservatively and vetoed all index options trades. 
2. **Pure Breakout Success**: Standard rule-based entry successfully captured clean directional trends, securing a **100% win rate** and **₹+18,592.00** net profits.
3. **Actionable Strategy**: It is highly recommended to bypass AI review or use a higher confluence floor specifically for high-liquidity index option breakouts.


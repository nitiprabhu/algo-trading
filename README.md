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

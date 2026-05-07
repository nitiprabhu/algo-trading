# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Backend**
```bash
pip install -e ".[dev]"
uvicorn services.chartedge_core.api:app --reload --port 8000
```

**Frontend**
```bash
cd frontend
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

**Tests**
```bash
pytest                          # all tests
pytest tests/test_ai_signal.py  # single file
```

**Lint**
```bash
ruff check .
ruff format .
```

**Backtest**
```bash
python run_today_backtest.py           # today's data backtest
python services/chartedge_core/backtest_runner.py  # full backtest runner
```

**Docker**
```bash
docker compose up                      # spins api (8000) + frontend (3000)
```

## Architecture

Single-process FastAPI backend (`services/chartedge_core/api.py`) runs everything in-process. On startup it spawns one `runtime.run()` background task that is either `MarketSimulator` (mock) or `IndstocksMarketRuntime` (live), selected by `CHARTEDGE_DATA_SOURCE` env var.

**Signal pipeline (per tick):**
1. `indstocks.py` / `simulation.py` — feeds raw candles into runtime
2. `indicators.py` — computes RSI, MACD, EMA ribbon, VWAP, Supertrend, volume, ATR, Bollinger per candle
3. `confluence.py` — weighted score → BUY/SELL/HOLD direction
4. `ai_signal.py` — sends snapshot to AI provider (Anthropic or OpenAI) via `prompt_builder.py`; falls back to `rule_based` when API key absent
5. `paper_trading.py` — enforces confidence floor, one-position-per-instrument, SL/T1/T2, T1 breakeven trail, kill switch
6. `database.py` — persists signals and trades via SQLModel to `chartedge.db` (SQLite) or PostgreSQL when `DATABASE_URL` is set

**Config:** `shared/config.yaml` is the master config; loaded by `config.py`. `DATABASE_URL` enables DB-based config overrides at runtime.

**Instruments:** NIFTY and BANKNIFTY are `role: trading`; RELIANCE, HDFCBANK, INDIAVIX are `role: monitor` only.

**Indicator weights** and **confluence thresholds** are per-instrument in `shared/config.yaml` — tune there, not in code.

**AI providers** (`ai_signal.py`): `AnthropicProvider` and `OpenAIProvider` share an `AIProvider` ABC. Provider selected by `ai.provider` in config. Raw JSON returned from LLM is parsed into a `Signal` with `direction`, `confidence`, `entry_zone`, `reasoning`.

**Frontend** (`frontend/`) is Next.js + TypeScript. Connects to backend via REST (`/api/*`) and WebSocket (`/ws`) for live signal feed. `NEXT_PUBLIC_API_URL` must point to the backend.

**`derivative_manager.py`** — resolves nearest-expiry F&O instrument tokens for NIFTY/BANKNIFTY options/futures via INDstocks lookup; called by `indstocks.py` at startup.

**`optimization.py`** — offline weight/threshold tuner using backtest P&L as objective; not called from the live runtime.

**`training_logger.py`** — appends signal outcomes to `logs/` for future ML training; called from `paper_trading.py` when a trade closes.

**`backtest_runner.py` / `backtest_direct.py`** — replay historical candles through the full indicator→confluence→AI pipeline offline; results saved to `backtest_analysis*.json`.

**`scratch/`** — throwaway debug scripts, not production code.

## Key env vars

| Var | Purpose |
|-----|---------|
| `CHARTEDGE_DATA_SOURCE` | `mock` (default) or `indstocks` |
| `INDMONEY_TOKEN` | INDstocks API token for live data |
| `ANTHROPIC_API_KEY` | Enables Claude reasoning; absent → `AI_UNAVAILABLE` fallback |
| `OPENAI_API_KEY` | Alternative AI provider |
| `DATABASE_URL` | PostgreSQL URL; absent → SQLite `chartedge.db` |

Copy `.env.example` to `.env` for local secrets; `python-dotenv` loads it automatically.
